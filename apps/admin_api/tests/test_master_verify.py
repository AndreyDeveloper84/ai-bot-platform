"""GET/POST /api/v1/admin/masters/awaiting-verification/ (DRF-1597).

Мастер, заведённый через админку Ayla, доезжает синхронизацией и
рождается ``pending``: клиент его не видит, а сделать шаг могла только
Django-админка. Эти тесты держат обе половины починки — что владелица
салона МОЖЕТ провести мастера до гейта продажи, и что она НЕ МОЖЕТ
сделать это за человека, которому приглашение реально выписано.

Форма строки важна и здесь. Мастер, приехавший синхронизацией, —
это ``is_active=True``, ``ayla_user_id`` есть, ``invite_token IS NULL``:
``upsert_specialists`` платформенных полей не трогает, токена не
выписывает, а активность берёт из Ayla. Фикстура ``pending_master`` из
conftest описывает ДРУГОЙ случай — приглашённого через
``masters/invite/`` (``is_active=False`` и живой токен), — поэтому здесь
синхронизированная строка собирается явно, а не переиспользуется.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.master_state import is_available
from apps.catalog.models import CatalogMaster
from apps.catalog.services import verification
from apps.tenancy.models import Tenant

from .conftest import init_data_header

# Модуль импортируется целиком, а ``AUDIT_ACTION`` читается в теле
# теста. Это не стиль: до правки константы не существует, и импорт по
# имени уронил бы СБОР тестов. Тогда «краснеет до правки» означало бы
# «не собирается», а доказывать нужно поведение — что владелица не
# может провести мастера до продажи, потому что эндпойнта нет.

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse("admin_api:masters_awaiting_verification")


def _get(client: Client, *, user_id: str = "5001"):
    return client.get(_url(), HTTP_AUTHORIZATION=init_data_header(user_id))


def _post(client: Client, body: dict | None = None, *, user_id: str = "5001"):
    return client.post(
        _url(),
        data=json.dumps(body if body is not None else {}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _synced_master(
    tenant: Tenant,
    *,
    name: str = "Ольга Синхронная",
    external_id: int = 501,
    is_active: bool = True,
    archived_at: datetime | None = None,
    ayla_user_id: uuid.UUID | None = None,
) -> CatalogMaster:
    """Строка ровно той формы, какую делает синхронизация каталога.

    ``invite_token=None`` — несущее поле теста, а не заполнение по
    инерции: именно отсутствие токена отличает «мастера никто не
    приглашал» от «мастер ждёт своего нажатия».
    """

    now = datetime.now(tz=dt_timezone.utc)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        specialization="Маникюр",
        is_active=is_active,
        archived_at=archived_at,
        invite_status=CatalogMaster.InviteStatus.PENDING,
        invite_token=None,
        invited_at=None,
        ayla_user_id=ayla_user_id or uuid.uuid4(),
    )


def _invited_master(
    tenant: Tenant,
    *,
    name: str = "Дарья Приглашённая",
    external_id: int = 502,
    expires_in_days: int = 5,
) -> CatalogMaster:
    """Приглашённая лично, ПЛЮС склеенная синхронизацией (DRF-1507).

    Живой токен и при этом ``is_active`` с ``ayla_user_id`` — та самая
    строка, которая попадает в ``AWAITING_VERIFICATION`` и за которую
    решать нельзя. На пилоте сегодня таких нет; тест держит границу до
    того, как склейка их создаст.
    """

    now = datetime.now(tz=dt_timezone.utc)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.PENDING,
        invite_token=uuid.uuid4(),
        invited_at=now,
        invite_expires_at=now + timedelta(days=expires_in_days),
        ayla_user_id=uuid.uuid4(),
    )


class TestGateOpens:
    """Сквозной смысл задачи: мастер доходит до ``AVAILABLE``."""

    def test_owner_verifies_synced_master_and_it_becomes_sellable(
        self, client, owner_bot_user, tenant
    ):
        master = _synced_master(tenant)
        assert is_available(master) is False

        resp = _post(client, {"master_ids": [str(master.id)]})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["verified"] == 1
        assert body["blocked"] == 0
        assert body["remaining"] == 0

        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert is_available(master) is True

    def test_verify_all_when_no_ids_given(self, client, owner_bot_user, tenant):
        a = _synced_master(tenant, name="Первая", external_id=511)
        b = _synced_master(tenant, name="Вторая", external_id=512)

        resp = _post(client, {})

        assert resp.status_code == 200, resp.content
        assert resp.json()["verified"] == 2
        for m in (a, b):
            m.refresh_from_db()
            assert is_available(m) is True

    def test_second_press_is_skip_not_second_journal_row(self, client, owner_bot_user, tenant):
        master = _synced_master(tenant)
        _post(client, {"master_ids": [str(master.id)]})

        resp = _post(client, {"master_ids": [str(master.id)]})

        assert resp.status_code == 200, resp.content
        # Уже подтверждённая строка выпадает из очереди совсем, поэтому
        # выборка по id её не находит — verified и skipped оба нулевые.
        assert resp.json()["verified"] == 0
        assert AuditLog.all_tenants.filter(action=verification.AUDIT_ACTION).count() == 1


class TestQueue:
    def test_queue_lists_the_master_nobody_can_see(self, client, owner_bot_user, tenant):
        master = _synced_master(tenant)

        resp = _get(client)

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["count"] == 1
        assert [i["id"] for i in body["items"]] == [str(master.id)]
        assert body["items"][0]["name"] == master.name

    def test_queue_does_not_promise_archived_or_inactive(self, client, owner_bot_user, tenant):
        now = datetime.now(tz=dt_timezone.utc)
        _synced_master(tenant, name="В архиве", external_id=521, archived_at=now)
        _synced_master(tenant, name="Снята", external_id=522, is_active=False)

        resp = _get(client)

        assert resp.status_code == 200, resp.content
        assert resp.json()["count"] == 0

    def test_queue_hides_the_row_that_waits_for_the_master_herself(
        self, client, owner_bot_user, tenant
    ):
        _invited_master(tenant)

        resp = _get(client)

        assert resp.status_code == 200, resp.content
        assert resp.json()["count"] == 0

    def test_queue_empties_after_verification(self, client, owner_bot_user, tenant):
        _synced_master(tenant)
        assert _get(client).json()["count"] == 1

        _post(client, {})

        assert _get(client).json()["count"] == 0

    def test_another_salon_master_is_not_in_this_queue(
        self, client, owner_bot_user, tenant, other_tenant
    ):
        foreign = _synced_master(other_tenant, name="Чужая", external_id=531)

        resp = _get(client)

        assert resp.status_code == 200, resp.content
        assert resp.json()["count"] == 0
        foreign.refresh_from_db()
        assert foreign.invite_status == CatalogMaster.InviteStatus.PENDING


class TestTwoPathsNotOneLoophole:
    """Живой токен — граница между двумя путями, не перестраховка.

    Решение владельца 08.09.2026, дословно: «Согласие остаётся
    обязательным только для приглашённых извне, тем, кому реально
    выписывают токен». Салонный мастер и приглашённая извне идут разными
    путями, и различает их наличие живого токена — ровно это здесь и
    закреплено.
    """

    def test_live_invite_is_refused_by_name_not_by_silence(self, client, owner_bot_user, tenant):
        invited = _invited_master(tenant)

        resp = _post(client, {"master_ids": [str(invited.id)]})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        # Отказ НАЗВАН: blocked, а не «ничего не нашлось». Владелица
        # обязана прочитать «за этого человека решать нельзя», а не
        # «ничего не произошло».
        assert body["blocked"] == 1
        assert body["verified"] == 0

        # Заблокированная строка НЕ продаётся — и это замысел, а не
        # расхождение гейта. `still_hidden` о ней молчит, иначе он кричал
        # бы на каждой штатной границе согласия и перестал бы значить
        # что-либо в тот единственный раз, когда важен.
        assert body["still_hidden"] == []

        invited.refresh_from_db()
        assert invited.invite_status == CatalogMaster.InviteStatus.PENDING
        assert is_available(invited) is False
        assert not AuditLog.all_tenants.filter(action=verification.AUDIT_ACTION).exists()

    def test_expired_invite_is_not_a_live_one(self, client, owner_bot_user, tenant):
        """Протухшее приглашение уже никто не примет — строка не заперта.

        Иначе мастер с протухшим токеном остался бы невидимым навсегда:
        принять его она не может, подтвердить владелица не может.
        """

        stale = _invited_master(tenant, name="Протухшая", external_id=541, expires_in_days=-1)

        resp = _post(client, {"master_ids": [str(stale.id)]})

        assert resp.status_code == 200, resp.content
        assert resp.json()["verified"] == 1
        stale.refresh_from_db()
        assert is_available(stale) is True

    def test_bulk_verify_walks_around_nobody(self, client, owner_bot_user, tenant):
        """«Подтвердить всех» не задевает того, кто ждёт своего нажатия."""

        synced = _synced_master(tenant)
        invited = _invited_master(tenant)

        resp = _post(client, {})

        assert resp.status_code == 200, resp.content
        assert resp.json()["verified"] == 1
        synced.refresh_from_db()
        invited.refresh_from_db()
        assert synced.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert invited.invite_status == CatalogMaster.InviteStatus.PENDING


class TestNoSilentSkips:
    """У каждого пропуска есть имя (OPEN_DECISIONS §78).

    Форма заведения в PR #300 принимала POST, отвечала «сохранено» и
    молча теряла привязку салона. Та же форма дефекта здесь выглядела бы
    так: владелица назвала мастера, получила 200 и «подтверждено: 0» —
    и не узнала, ни что произошло, ни что делать.
    """

    def test_named_master_outside_the_queue_is_counted_not_swallowed(
        self, client, owner_bot_user, tenant
    ):
        now = datetime.now(tz=dt_timezone.utc)
        archived = _synced_master(tenant, name="В архиве", external_id=551, archived_at=now)

        resp = _post(client, {"master_ids": [str(archived.id)]})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["not_eligible"] == 1
        assert body["verified"] == 0

    def test_unknown_id_is_counted_too(self, client, owner_bot_user, tenant):
        resp = _post(client, {"master_ids": [str(uuid.uuid4())]})

        assert resp.status_code == 200, resp.content
        assert resp.json()["not_eligible"] == 1

    def test_the_same_id_twice_is_one_master_not_one_skip(self, client, owner_bot_user, tenant):
        """Дубль в списке — не пропуск.

        Считай ``not_eligible`` длиной списка, и повторно названный
        мастер выдал бы «подтверждён 1, не подошёл 1» про одного и того
        же человека.
        """

        master = _synced_master(tenant)

        resp = _post(client, {"master_ids": [str(master.id), str(master.id)]})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["verified"] == 1
        assert body["not_eligible"] == 0

    def test_another_salon_master_named_by_id_is_counted_not_verified(
        self, client, owner_bot_user, tenant, other_tenant
    ):
        foreign = _synced_master(other_tenant, name="Чужая", external_id=552)

        resp = _post(client, {"master_ids": [str(foreign.id)]})

        assert resp.status_code == 200, resp.content
        assert resp.json()["not_eligible"] == 1
        foreign.refresh_from_db()
        assert foreign.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_verified_masters_are_reported_as_actually_sellable(
        self, client, owner_bot_user, tenant
    ):
        """Обещание кнопки проверено замером, а не выведено из предиката."""

        _synced_master(tenant)

        resp = _post(client, {})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["verified"] == 1
        assert body["still_hidden"] == []
        assert body["not_eligible"] == 0


class TestWhoMayPress:
    def test_admin_reads_the_queue(self, client, admin_bot_user, tenant):
        _synced_master(tenant)

        resp = _get(client, user_id="5002")

        assert resp.status_code == 200, resp.content
        assert resp.json()["count"] == 1

    def test_admin_may_not_verify(self, client, admin_bot_user, tenant):
        master = _synced_master(tenant)

        resp = _post(client, {}, user_id="5002")

        assert resp.status_code == 403, resp.content
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_receptionist_is_refused_at_the_door(self, client, receptionist_bot_user, tenant):
        _synced_master(tenant)

        assert _get(client, user_id="5003").status_code == 403
        assert _post(client, {}, user_id="5003").status_code == 403


class TestTrace:
    def test_verification_leaves_an_author(self, client, owner_bot_user, tenant):
        master = _synced_master(tenant)

        _post(client, {"master_ids": [str(master.id)]})

        row = AuditLog.all_tenants.get(action=verification.AUDIT_ACTION)
        assert row.actor_id == owner_bot_user.id
        assert str(row.target_id) == str(master.id)
        assert "Верификация вручную" in row.payload["change_message"]


class TestRequestShape:
    def test_master_ids_must_be_a_list(self, client, owner_bot_user, tenant):
        resp = _post(client, {"master_ids": "not-a-list"})
        assert resp.status_code == 400, resp.content

    def test_empty_master_ids_is_refused_not_read_as_all(self, client, owner_bot_user, tenant):
        """``[]`` и «поле не передано» — разные намерения.

        Пустой список, прочитанный как «все», подтвердил бы весь салон
        по запросу, который просил не подтверждать никого.
        """

        master = _synced_master(tenant)

        resp = _post(client, {"master_ids": []})

        assert resp.status_code == 400, resp.content
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_garbage_id_does_not_500(self, client, owner_bot_user, tenant):
        resp = _post(client, {"master_ids": ["не-uuid"]})
        assert resp.status_code == 400, resp.content
