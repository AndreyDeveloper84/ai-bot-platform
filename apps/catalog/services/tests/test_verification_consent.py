"""Согласие мастера при верификации приглашения (DRF-1597).

Держит две границы, и обе — ЗДЕСЬ, в сервисе, а не на эндпойнте:
эндпойнт обходится вторым экраном, сервис — нет.

1. **Синхронизация не проставляет ``accepted``.** Умолчание ``pending``
   стоит осознанным решением (DRF-1496): приглашение существует, чтобы
   мастер согласился сам. Автоприём на прогоне обошёл бы согласие молча
   и для всех сразу.

2. **Строку с живым персональным приглашением не подтверждает никто.**
   Ни владелица салона, ни оператор, ни суперюзер: приглашение выписано
   человеку, и решать за него нельзя независимо от того, у кого больше
   прав.

Модуль намеренно импортирует только те имена, которые существовали ДО
правки (``verify_masters``, ``upsert_specialists``). Новые входы сервиса
проверяет соседний ``test_verification_salon_path.py``. Разделение не
косметическое: тест на обход согласия обязан краснеть НА ПОВЕДЕНИИ —
«суперюзер принял приглашение за мастера», — а не на импорте символа,
которого до правки нет. Красное «не собирается» ничего не доказывает.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO
from apps.catalog.services.upserter import upsert_specialists
from apps.catalog.services.verification import verify_masters
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="consent-tenant", name="Салон Согласия")


def _dto(*, ayla_master_id: str, user_id: str, name: str = "Ольга") -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=ayla_master_id,
        user_id=user_id,
        name=name,
        external_updated_at=NOW,
        is_active=True,
    )


def _invited(tenant: Tenant, *, external_id: int, expires_in_days: int = 3) -> CatalogMaster:
    """Строка, которой приглашение реально выписано.

    ``is_active`` и ``ayla_user_id`` есть — так выглядит приглашённая
    мастер после того, как синхронизация склеила её со строкой из Ayla
    (``upsert_specialists`` ищет по ``ayla_user_id``, DRF-1507). Именно в
    этой форме она попадает в очередь на подтверждение, и именно здесь
    за неё нельзя решать.
    """

    now = datetime.now(tz=dt_timezone.utc)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=NOW,
        name="Дарья Приглашённая",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.PENDING,
        invite_token=uuid.uuid4(),
        invited_at=now,
        invite_expires_at=now + timedelta(days=expires_in_days),
        ayla_user_id=uuid.uuid4(),
    )


class TestSyncNeverAccepts:
    """Автоприёма при синхронизации нет и быть не должно."""

    def test_new_master_is_born_pending(self, tenant):
        ayla_id = str(uuid.uuid4())

        upsert_specialists(tenant, [_dto(ayla_master_id=ayla_id, user_id=str(uuid.uuid4()))])

        master = CatalogMaster.all_tenants.get(pk=ayla_id)
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        # Токена нет — приглашать её никто не приглашал, и принимать ей
        # нечего. Именно это отличает её от строки из ``masters/invite/``.
        assert master.invite_token is None

    def test_rerun_does_not_promote_a_pending_master(self, tenant):
        ayla_id = str(uuid.uuid4())
        dto = _dto(ayla_master_id=ayla_id, user_id=str(uuid.uuid4()))
        upsert_specialists(tenant, [dto])

        upsert_specialists(tenant, [dto])

        master = CatalogMaster.all_tenants.get(pk=ayla_id)
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_sync_does_not_undo_a_confirmed_master_either(self, tenant):
        """Граница симметрична: синхронизация не откатывает подтверждение.

        Иначе первый же прогон после нажатия владелицы снял бы мастера с
        продажи обратно, и кнопка обещала бы то, что живёт до следующего
        такта синхронизации.
        """

        ayla_id = str(uuid.uuid4())
        dto = _dto(ayla_master_id=ayla_id, user_id=str(uuid.uuid4()))
        upsert_specialists(tenant, [dto])
        master = CatalogMaster.all_tenants.get(pk=ayla_id)
        master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        master.save(update_fields=["invite_status"])

        upsert_specialists(tenant, [dto])

        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED


class TestNobodyAcceptsForTheInvitedMaster:
    def test_superuser_operator_cannot_accept_a_live_invite(self, tenant):
        """Граница по СТРОКЕ, а не по правам нажавшего.

        Суперюзер здесь — самый сильный из возможных авторов, и именно
        поэтому он в тесте: разреши обход «тому, у кого есть права», и
        запрет держался бы ровно до первого человека с доступом к
        серверу.
        """

        operator = get_user_model().objects.create_superuser(
            username="operator-consent", email="op@example.com", password="x"
        )
        invited = _invited(tenant, external_id=201)

        verify_masters([invited], user=operator)

        invited.refresh_from_db()
        assert invited.invite_status == CatalogMaster.InviteStatus.PENDING
        assert invited.accepted_at is None

    def test_expired_invite_is_not_a_live_one(self, tenant):
        """Протухшее приглашение уже никто не примет — строка не заперта.

        Иначе мастер с протухшим токеном осталась бы невидимой навсегда:
        принять его она не может, подтвердить — никто.
        """

        operator = get_user_model().objects.create_superuser(
            username="operator-stale", email="op2@example.com", password="x"
        )
        stale = _invited(tenant, external_id=202, expires_in_days=-1)

        verify_masters([stale], user=operator)

        stale.refresh_from_db()
        assert stale.invite_status == CatalogMaster.InviteStatus.ACCEPTED
