"""Integration tests for POST /api/v1/admin/masters/invite (PR 3 / MM2).

Covers:

* Auth matrix — Owner allowed, Admin allowed, Receptionist 403,
  Customer 403.
* Validation — missing name, bad contact_method, oversized fields,
  cross-tenant service UUID, role!=master.
* Side effects — CatalogMaster created with token + TTL, MasterService
  rows seeded, ONE audit row (``master.invited``).
* Idempotency — same (name, contact_value) returns existing row +
  ``X-Idempotent`` header; expired invite creates fresh; different
  contact_value creates fresh.
* Atomic — service seed failure rolls back master row.
* Modes — ``catalog_only`` skips the token.

Личного сообщения этот эндпоинт больше не шлёт (решение владельца §44.4
от 07.09.2026), поэтому здесь нет ни `patched_send_message`, ни класса
про доставку. Стража, что попытка не вернулась, стоит рядом — в
``test_invite_no_dm.py``: она бьёт по ``max_outbound``, а не по
отсутствию строки в ответе, потому что вернуть отправку можно и не
трогая ответ вовсе.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant


# --- URL helper -----------------------------------------------------------


def _invite_url() -> str:
    return reverse("admin_api:master_invite_create")


# --- shared body builder --------------------------------------------------


def _valid_body(
    *,
    name: str = "Анна Петрова",
    contact_method: str = "max_username",
    contact_value: str = "@anna_styl",
    services: list[str] | None = None,
    schedule_preset: str = "default_mon_fri_10_19",
    mode: str = "invite",
    role: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "contact_method": contact_method,
        "contact_value": contact_value,
        "services": services or [],
        "schedule_preset": schedule_preset,
        "mode": mode,
    }
    if role is not None:
        out["role"] = role
    return out


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestAuth:
    def test_missing_auth_header_400(self, client: Client, tenant: Tenant) -> None:
        resp = client.post(_invite_url(), data=_valid_body(), content_type="application/json")
        assert resp.status_code == 400

    def test_customer_403(
        self,
        client: Client,
        customer_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5005"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_receptionist_403(
        self,
        client: Client,
        receptionist_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )
        assert resp.status_code == 403

    def test_admin_allowed(
        self,
        client: Client,
        admin_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="Admin Issued"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 201, resp.content

    def test_owner_allowed(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="Owner Issued"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content


# =========================================================================
# HAPPY PATH — full side-effect verification
# =========================================================================


class TestHappyPath:
    def test_creates_master_with_token_and_ttl(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        # DRF-1079 — the assertion below says «uses configured
        # SITE_DOMAIN», and until this line nothing configured it: the
        # test passed against the repository default, i.e. against the
        # localhost link that reached real invited masters on the pilot.
        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"
        before = datetime.now(tz=timezone.utc)
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        after = datetime.now(tz=timezone.utc)
        assert resp.status_code == 201, resp.content
        body = resp.json()

        master = CatalogMaster.all_tenants.get(id=body["master_id"])
        assert master.tenant_id == tenant.id
        assert master.name == "Анна Петрова"
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.is_active is False
        assert master.mode == CatalogMaster.Mode.INVITE
        assert master.max_handle == "@anna_styl"
        assert master.invite_token is not None
        assert str(master.invite_token) == body["invite_token"]

        # TTL ≈ now + 7 days (allow a generous 60s slack for slow CI).
        assert master.invite_expires_at is not None
        assert master.invite_expires_at > before + timedelta(days=7) - timedelta(minutes=1)
        assert master.invite_expires_at < after + timedelta(days=7) + timedelta(minutes=1)

        # fallback_link includes the token + uses configured SITE_DOMAIN.
        assert body["fallback_link"].endswith(f"?token={master.invite_token}")
        assert "/onboarding/master" in body["fallback_link"]

    def test_default_preset_no_longer_seeds_working_hours(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """DRF-1062: the invite must not manufacture a schedule.

        This branch used to bulk-create Mon-Fri 10:00-19:00 — the stub all
        four pilot masters now carry, indistinguishable from a schedule the
        salon actually set. It also shaped nothing: with
        BOOKING_VIA_AYLA_REST the backend serves slots, not
        `apps.scheduling`.

        `schedule_preset` stays in the request contract and is still echoed
        back; it simply has no side effect now.
        """

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        # Accepted with the preset still in the request contract...
        assert resp.status_code == 201
        # ...and no schedule manufactured behind it.
        master_id = uuid.UUID(resp.json()["master_id"])
        assert not WorkingHours.all_tenants.filter(master_id=master_id).exists()

    def test_schedule_preset_none_seeds_zero_working_hours(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(schedule_preset="none"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        assert WorkingHours.all_tenants.filter(master_id=master_id).count() == 0

    def test_services_array_creates_master_services(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        rows = list(MasterService.all_tenants.filter(master_id=master_id))
        assert len(rows) == 1
        assert rows[0].service_id == service.id
        # DRF-975 — the invite seeder is a bulk_create, which sends no
        # pre_save; its gate is MasterServiceQuerySet.bulk_create. Asserting
        # the specific source proves the view declared itself rather than
        # inheriting the suite-wide ambient TEST_FIXTURE context.
        assert rows[0].source == "invite_seed"
        assert rows[0].created_by_actor_id == owner_bot_user.id

    def test_one_audit_row_emitted(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Одна строка, а не две.

        Вторая была ``master.invite_dispatched`` — исход отправки
        личного сообщения. Отправки нет (§44.4), и записи о ней тоже:
        аудит-строка «skipped» описывала бы событие, которого не бывает.
        """

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        actions = sorted(
            AuditLog.all_tenants.filter(target_id=master_id).values_list("action", flat=True)
        )
        assert actions == ["master.invited"]

    def test_audit_payload_shape_invited(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        master_id = uuid.UUID(resp.json()["master_id"])
        row = AuditLog.all_tenants.get(target_id=master_id, action="master.invited")
        payload = row.payload
        assert payload["master_id"] == str(master_id)
        assert payload["actor_id"] == str(owner_bot_user.id)
        assert payload["actor_role"] == "owner"
        assert payload["role"] == "master"
        assert payload["contact_method"] == "max_username"
        assert payload["mode"] == "invite"
        assert payload["services_count"] == 1


# =========================================================================
# VALIDATION
# =========================================================================


class TestValidation:
    def test_missing_name_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        body = _valid_body()
        del body["name"]
        resp = client.post(
            _invite_url(),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["detail"]

    def test_blank_name_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="   "),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_bad_contact_method_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_method="email"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        # Verify the error mentions email is deferred.
        assert "email" in resp.json()["detail"].lower()

    def test_max_phone_contact_stored_in_raw(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """CatalogMaster has no phone column; we stash phone in raw["invite_phone"]."""

        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_method="max_phone", contact_value="+79161234567"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        master = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert master.raw.get("invite_phone") == "+79161234567"
        assert master.max_handle == ""  # max_phone does NOT populate max_handle

    def test_cross_tenant_service_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        foreign_service = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=999,
            external_updated_at=now,
            slug="foreign",
            name="Foreign",
            duration_min=30,
            is_active=True,
        )
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(foreign_service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "service not in tenant" in resp.json()["detail"]

    def test_role_admin_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """PR 3 scope: admin/receptionist invite writes TenantStaff, not CatalogMaster."""

        resp = client.post(
            _invite_url(),
            data=_valid_body(role="admin"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "master" in resp.json()["detail"].lower()

    def test_bad_schedule_preset_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(schedule_preset="custom"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_bad_mode_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(mode="anonymous"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_catalog_only_mode_issues_no_token(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(mode="catalog_only"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["invite_token"] is None
        assert body["invite_expires_at"] is None
        assert body["fallback_link"] == ""

        master = CatalogMaster.all_tenants.get(id=body["master_id"])
        assert master.mode == CatalogMaster.Mode.CATALOG_ONLY
        assert master.invite_token is None
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.is_active is False


# =========================================================================
# IDEMPOTENCY
# =========================================================================


class TestIdempotency:
    def test_same_name_and_contact_returns_existing_200(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # First call — 201
        first = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert first.status_code == 201
        first_master_id = first.json()["master_id"]
        first_token = first.json()["invite_token"]

        # Second call — same body → 200 with X-Idempotent header.
        second = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert second.status_code == 200, second.content
        assert second["X-Idempotent"] == "true"
        body = second.json()
        assert body["master_id"] == first_master_id
        assert body["invite_token"] == first_token

        # Only one CatalogMaster row.
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_expired_invite_reissues_on_the_same_row(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """DRF-1507 — повторное приглашение работает и НЕ заводит вторую строку.

        Ожидание этого теста изменено намеренно. До DRF-1507 он назывался
        ``test_expired_invite_creates_new_row`` и требовал «Now 2 rows in
        catalog»: путь заводил вторую строку с тем же ``max_handle``, и это
        считалось контрактом, пока дубли считались приемлемыми. Смысл всей
        задачи — «один человек, одна строка», поэтому вторая строка стала
        дефектом.

        Что НЕ изменилось и проверяется здесь же: владелец по-прежнему
        получает 201 и рабочий свежий токен с новым семидневным сроком.
        Что этот токен доводит мастера до кабинета — проверяет
        ``apps/master_api/tests/test_onboarding.py``.
        """

        now = datetime.now(tz=timezone.utc)
        stale = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11111,
            external_updated_at=now,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
            invite_expires_at=now - timedelta(hours=1),
            invited_at=now - timedelta(days=8),
            max_handle="@anna_styl",
            mode=CatalogMaster.Mode.INVITE,
            is_active=False,
        )
        stale_token = stale.invite_token

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        # Свежее приглашение выписано — 201, не 200: это новое приглашение,
        # а не повтор того же запроса.
        assert resp.status_code == 201
        # Присутствие прежде отсутствия (DRF-1411): тело есть и оно про
        # ту же строку — только после этого «заголовка нет» что-то значит.
        assert resp.json()["master_id"] == str(stale.id)
        assert "X-Idempotent" not in resp
        # И выписано оно НА ТУ ЖЕ строку.
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

        stale.refresh_from_db()
        assert stale.invite_status == CatalogMaster.InviteStatus.PENDING
        assert stale.invite_token is not None
        assert stale.invite_token != stale_token
        assert stale.invite_expires_at is not None
        assert stale.invite_expires_at > now
        assert str(stale.invite_token) == resp.json()["invite_token"]

    def test_different_person_still_gets_their_own_row(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Положительная стража к тесту выше (DRF-1411).

        Переиспользование строки обязано срабатывать на ТОМ ЖЕ человеке и
        не срабатывать на другом: иначе «одна строка на человека»
        превратилась бы в «один мастер на салон».
        """

        now = datetime.now(tz=timezone.utc)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11111,
            external_updated_at=now,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
            invite_expires_at=now - timedelta(hours=1),
            invited_at=now - timedelta(days=8),
            max_handle="@anna_styl",
            mode=CatalogMaster.Mode.INVITE,
            is_active=False,
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(name="Мария Иванова", contact_value="@maria_nails"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_handle_written_without_at_is_the_same_person(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """«anna_styl» и «@Anna_Styl» — один аккаунт MAX, а не два мастера."""

        now = datetime.now(tz=timezone.utc)
        stale = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11111,
            external_updated_at=now,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
            invite_expires_at=now - timedelta(hours=1),
            invited_at=now - timedelta(days=8),
            max_handle="@Anna_Styl",
            mode=CatalogMaster.Mode.INVITE,
            is_active=False,
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_value="anna_styl"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        assert resp.json()["master_id"] == str(stale.id)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_synced_row_is_not_turned_back_into_a_pending_invite(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Отбор на перевыпуск узкий — синхронизированная строка не трогается.

        Положительная стража к переиспользованию: если бы отбор шёл по
        «есть handle и нет связи», приглашение мастера, приехавшего
        синхронизацией, перевело бы его строку в PENDING — и он выпал бы
        из записи (``booking/services/create.py`` требует ACCEPTED) до
        того, как откроет ссылку.
        """

        now = datetime.now(tz=timezone.utc)
        synced = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=7,
            external_updated_at=now,
            name="Анна Петрова",
            is_active=True,
            ayla_user_id=uuid.uuid4(),
            max_handle="@anna_styl",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            mode=CatalogMaster.Mode.CATALOG_ONLY,
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["master_id"] != str(synced.id)

        synced.refresh_from_db()
        assert synced.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert synced.invite_token is None
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_already_landed_master_is_not_invited_twice(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Приглашение уже приземлившегося мастера сводится в его строку.

        Второй токен на того же человека — это вторая строка, которая
        останется PENDING навсегда: ``onboarding_accept`` вернёт ему сессию
        первой (разрыв Р5). Поэтому 200 и существующая строка.
        """

        now = datetime.now(tz=timezone.utc)
        landed_bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="777001",
            chat_id="777001",
        )
        landed = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11111,
            external_updated_at=now,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            max_handle="@anna_styl",
            mode=CatalogMaster.Mode.INVITE,
            is_active=True,
            linked_bot_user=landed_bot_user,
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        assert resp["X-Idempotent"] == "true"
        assert resp.json()["master_id"] == str(landed.id)
        assert resp.json()["invite_token"] is None
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_different_contact_value_same_name_creates_new(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp1 = client.post(
            _invite_url(),
            data=_valid_body(contact_value="@anna_styl"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            _invite_url(),
            data=_valid_body(contact_value="@anna_other"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp2.status_code == 201
        assert resp1.json()["master_id"] != resp2.json()["master_id"]


# =========================================================================
# ATOMICITY
# =========================================================================


class TestAtomicity:
    def test_service_seeding_failure_rolls_back_master(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
    ) -> None:
        """A failure inside the transaction must not leave a half-made master.

        Was written against WorkingHours seeding, which DRF-1062 removed.
        Re-pointed at MasterService — the remaining bulk write in the same
        atomic block — so the rollback guarantee stays covered rather than
        quietly disappearing with the branch it happened to test.
        """

        before = CatalogMaster.all_tenants.filter(tenant=tenant).count()
        with patch("apps.admin_api.views_invite.MasterService.all_tenants") as mock_ms:
            mock_ms.bulk_create.side_effect = RuntimeError("forced failure")
            resp = client.post(
                _invite_url(),
                # Non-empty services: _seed_services short-circuits on an
                # empty list, so an empty body would never reach bulk_create
                # and the test would pass without exercising anything.
                data=_valid_body(services=[str(service.id)]),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )

        assert resp.status_code == 500
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == before


# --- DRF-1079: the web fallback must never point at localhost -------------


@pytest.mark.django_db
class TestSiteDomainFallback:
    """The invite link is the one artefact of this endpoint a human uses.

    On the pilot ``SITE_DOMAIN`` was never set, so every invite carried
    ``http://localhost:5173/onboarding/master?token=...`` — a link that
    opens nothing on the phone it arrives at, with no error anywhere on
    our side. These tests pin the two halves of the fix: a real domain
    produces a real link, an unset one produces no link at all rather
    than a broken one.
    """

    def test_configured_domain_is_used(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        link = resp.json()["fallback_link"]
        assert link.startswith("https://miniapp-dev.gobeauty.site/onboarding/master?token=")

    def test_bare_host_gets_https(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """A value written without a scheme must not become a relative URL."""

        settings.SITE_DOMAIN = "miniapp-dev.gobeauty.site"
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["fallback_link"].startswith(
            "https://miniapp-dev.gobeauty.site/onboarding/master?token="
        )

    def test_loopback_domain_suppresses_the_link(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """The pilot's actual state: SITE_DOMAIN unset, DEBUG off.

        Отдаётся пустая строка, а не ссылка на ``localhost:5173``:
        правдоподобный мёртвый адрес хуже отсутствия — владелец
        отправит его мастеру и узнает об этом от мастера.

        Раньше этот тест кончался на ``max_dm_delivery == "failed"``:
        без Mini App и без домена личному сообщению нечего было нести,
        и оно не уходило. Личного сообщения больше нет вовсе (§44.4),
        так что проверять здесь осталось ровно одно — какой адрес
        видит владелец. Спутник ниже держит вторую половину: пустой
        ``SITE_DOMAIN`` сам по себе приглашение не ломает.
        """

        settings.SITE_DOMAIN = ""
        settings.MAX_BOT_WEB_APP = ""
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        # Присутствие на тех же данных: строка мастера выписана и токен
        # у неё есть — иначе «ссылки нет» доказывало бы только то, что
        # приглашение вообще не создалось.
        assert body["invite_token"]
        assert body["fallback_link"] == ""

    def test_unset_domain_is_harmless_when_the_mini_app_is_configured(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """SITE_DOMAIN is the *web fallback's* artefact, not the invite's.

        The positive guard for the test above. Приглашение живёт в
        ``invite_link`` — ссылке на салонного бота, которая от
        ``SITE_DOMAIN`` не зависит вовсе. Без этой проверки предыдущий
        тест читался бы как «нет домена ⇒ нет приглашения», а это ровно
        обратный вывод.
        """

        from apps.channels.bot_registry import BotEntry

        settings.SITE_DOMAIN = ""
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                webhook_secret="wh-salon",  # pragma: allowlist secret
                api_token="token-salon",  # pragma: allowlist secret
                tenant_slug="admin-api-test",
                stream="max_salon",
                web_app="id583403546770_3_bot",
            ),
        )
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["invite_link"].startswith("https://max.ru/")
        assert "master_invite_" in body["invite_link"]
        # Ни одного адреса, который открывается только на машине
        # разработчика: пустой SITE_DOMAIN гасит веб-запасной путь, а не
        # подменяет его localhost-ом.
        assert "localhost" not in body["invite_link"]
        assert body["fallback_link"] == ""

    def test_debug_keeps_the_localhost_link(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """Local dev is the one place the Vite URL is the right answer."""

        settings.DEBUG = True
        settings.SITE_DOMAIN = ""
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["fallback_link"].startswith(
            "http://localhost:5173/onboarding/master?token="
        )

    def test_system_check_flags_the_unset_domain(self, settings) -> None:
        """`manage.py check` / `migrate` is where the deploy sees it."""

        from apps.admin_api.checks import check_site_domain

        settings.SITE_DOMAIN = ""
        warnings = check_site_domain(None)
        assert [w.id for w in warnings] == ["admin_api.W001"]

        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"
        assert check_site_domain(None) == []

    def test_hint_names_the_mini_app_origin_not_the_backend(self, settings, caplog) -> None:
        """The hint is the fix's other half, and it was wrong.

        Both statements of it — the system-check hint and the runtime
        ERROR line — named ``api-dev.gobeauty.site``. That host is the
        Django backend; it answers 404 on ``/onboarding/master``,
        because the route is a client-side route of the Mini App SPA
        (``apps/miniapp/src/App.tsx`` →  ``MasterOnboardingScreen``).
        Verified by live request 2026-08-25: ``api-dev`` 404,
        ``miniapp-dev`` 200.

        Suppressing the localhost link protects the master who was
        already invited. The hint decides what the *next* person types
        into ``.env.staging`` — and pointing them at the backend
        reproduces the same dead link with a domain that looks right.
        So it is pinned, in both places it is read.
        """

        import logging

        from apps.admin_api.checks import check_site_domain
        from apps.admin_api.views_invite import (
            PILOT_SITE_DOMAIN,
            SITE_DOMAIN_HINT,
            _fallback_link,
        )
        import uuid as _uuid

        assert PILOT_SITE_DOMAIN == "https://miniapp-dev.gobeauty.site"
        assert PILOT_SITE_DOMAIN in SITE_DOMAIN_HINT
        # Naming the backend host is allowed only as the negative
        # example — never as the value to set.
        assert "NOT the backend host https://api-dev.gobeauty.site" in SITE_DOMAIN_HINT

        settings.DEBUG = False
        settings.SITE_DOMAIN = ""

        (warning,) = check_site_domain(None)
        # `CheckMessage.hint` is `str | None`; the check is worthless if the
        # hint is missing, so assert its presence rather than narrowing with
        # a cast — a `type: ignore` here would record an assumption nobody
        # would ever verify.
        assert warning.hint is not None
        assert PILOT_SITE_DOMAIN in warning.hint

        with caplog.at_level(logging.ERROR, logger="apps.admin_api.views_invite"):
            assert _fallback_link(_uuid.uuid4()) == ""
        assert PILOT_SITE_DOMAIN in caplog.text


# =========================================================================
# EXTERNAL_ID — гонка (DRF-1507, пункт 3 / разрыв Р7)
# =========================================================================


class TestExternalIdRace:
    """``external_id`` больше не считается ``count()`` и умеет повторяться.

    Было: ``external_id = count(мастеров тенанта) + 1_000_000`` и общий
    ``except Exception`` → **500 без ретрая**. Два одновременных
    приглашения в одном салоне считают одно и то же число; хуже,
    ``count()`` совпадает и НЕ одновременно — достаточно, чтобы строку
    удалили.
    """

    def test_deleted_row_does_not_make_the_next_invite_collide(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Детерминированное воспроизведение той же гонки, без потоков.

        Со старым ``count()+1_000_000`` третье приглашение получало номер,
        который уже занят вторым, ловило ``unique_together (tenant,
        external_id)`` и отвечало 500. Здесь оно обязано ответить 201.
        """

        first = client.post(
            _invite_url(),
            data=_valid_body(name="Анна Петрова", contact_value="@anna_styl"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        second = client.post(
            _invite_url(),
            data=_valid_body(name="Мария Иванова", contact_value="@maria_nails"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert first.status_code == 201, first.content
        assert second.status_code == 201, second.content

        CatalogMaster.all_tenants.filter(id=first.json()["master_id"]).delete()

        third = client.post(
            _invite_url(),
            data=_valid_body(name="Ольга Смирнова", contact_value="@olga_brows"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert third.status_code == 201, third.content

        ids = set(
            CatalogMaster.all_tenants.filter(tenant=tenant).values_list("external_id", flat=True)
        )
        assert len(ids) == CatalogMaster.all_tenants.filter(tenant=tenant).count()

    def test_collision_is_retried_and_answers_201(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Собственно гонка: номер занят между чтением и вставкой.

        Уникальность держит база; проверяется здесь ответ на её
        срабатывание — пересчитать и повторить, а не 500.
        """

        now = datetime.now(tz=timezone.utc)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=1_000_000,
            external_updated_at=now,
            name="Уже занятый номер",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            max_handle="",
        )

        with patch(
            "apps.admin_api.views_invite._next_external_id",
            side_effect=[1_000_000, 1_000_001],
        ) as spy:
            resp = client.post(
                _invite_url(),
                data=_valid_body(),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )

        assert resp.status_code == 201, resp.content
        assert spy.call_count == 2
        created = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert created.external_id == 1_000_001

    def test_collision_that_never_clears_answers_500_not_a_loop(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Положительная стража к ретраю: он ограничен, а не бесконечен."""

        now = datetime.now(tz=timezone.utc)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=1_000_000,
            external_updated_at=now,
            name="Уже занятый номер",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            max_handle="",
        )

        with patch(
            "apps.admin_api.views_invite._next_external_id",
            return_value=1_000_000,
        ) as spy:
            resp = client.post(
                _invite_url(),
                data=_valid_body(),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )

        assert resp.status_code == 500
        assert spy.call_count == 5

    def test_invite_numbering_ignores_the_synced_range(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Номера Ayla не втягиваются в нашу нумерацию.

        Синхронизированная строка с ``external_id=42`` не должна ни
        сдвигать наш счётчик, ни быть им затронутой.
        """

        now = datetime.now(tz=timezone.utc)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=42,
            external_updated_at=now,
            name="Синхронизированная",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            max_handle="",
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        created = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert created.external_id == 1_000_000
