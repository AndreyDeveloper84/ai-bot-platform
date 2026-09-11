"""Вход владелицы салона в верификацию и его предикат (DRF-1597).

Отделено от ``test_verification_consent.py`` намеренно: здесь
импортируются символы, которых до правки не существует
(``verify_masters_by_salon``, ``has_live_invite``, ``live_invite_q``),
и «краснеет до правки» у этого файла означает «не собирается». Такое
красное ничего не доказывает, поэтому доказательство дефекта живёт в
соседнем модуле и на эндпойнте, а здесь — свойства нового кода.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.verification import (
    has_live_invite,
    live_invite_q,
    verify_masters_by_salon,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="salon-path", name="Салон Пути")


def _make(
    tenant: Tenant,
    *,
    external_id: int,
    invite_status: str = CatalogMaster.InviteStatus.PENDING,
    token: uuid.UUID | None = None,
    expires: datetime | None = None,
) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=NOW,
        name=f"Мастер {external_id}",
        is_active=True,
        invite_status=invite_status,
        invite_token=token,
        invite_expires_at=expires,
        ayla_user_id=uuid.uuid4(),
    )


class TestOwnerPath:
    def test_synced_master_without_a_token_is_confirmed(self, tenant):
        synced = _make(tenant, external_id=301)

        outcome = verify_masters_by_salon([synced], actor=None)

        assert outcome.verified == 1
        assert outcome.blocked == 0
        synced.refresh_from_db()
        assert synced.invite_status == CatalogMaster.InviteStatus.ACCEPTED

    def test_live_invite_is_counted_as_blocked_not_skipped(self, tenant):
        """``blocked`` и ``skipped`` — разные ответы человеку.

        «Уже принято» отправляет владелицу дальше, «за неё нельзя» —
        писать мастеру. Слить их в одно число значило бы сказать, что
        мастер подтверждён, когда он ждёт собственного нажатия.
        """

        invited = _make(
            tenant,
            external_id=302,
            token=uuid.uuid4(),
            expires=datetime.now(tz=dt_timezone.utc) + timedelta(days=3),
        )

        outcome = verify_masters_by_salon([invited], actor=None)

        assert (outcome.verified, outcome.skipped, outcome.blocked) == (0, 0, 1)

    def test_already_accepted_is_skipped_not_blocked(self, tenant):
        done = _make(tenant, external_id=303, invite_status=CatalogMaster.InviteStatus.ACCEPTED)

        outcome = verify_masters_by_salon([done], actor=None)

        assert (outcome.verified, outcome.skipped, outcome.blocked) == (0, 1, 0)

    def test_a_token_without_an_expiry_is_treated_as_live(self, tenant):
        """Отсутствие срока — не «истёк». Отказ в пользу согласия.

        Токен без срока боевой путь не выписывает, но если такая строка
        появится, читать пустой срок как «протухло» значило бы принять
        приглашение за человека на основании ПРОБЕЛА в данных.
        """

        odd = _make(tenant, external_id=304, token=uuid.uuid4(), expires=None)

        assert has_live_invite(odd) is True
        assert verify_masters_by_salon([odd], actor=None).blocked == 1


class TestQueueAndButtonAgree:
    def test_live_invite_query_matches_predicate(self, tenant):
        """Очередь и кнопка обязаны считать одни и те же строки.

        Разойдись ``live_invite_q`` с ``has_live_invite`` — и очередь
        пообещала бы владелице мастеров, которых кнопка потом не тронет
        (или спрятала бы тех, кого тронет). Это ровно та пятёрка
        разошедшихся определений, из-за которой заведён
        ``apps/catalog/master_state.py``.
        """

        now = datetime.now(tz=dt_timezone.utc)
        rows = [
            _make(tenant, external_id=310),  # синхронизация, без токена
            _make(tenant, external_id=311, token=uuid.uuid4(), expires=now + timedelta(days=1)),
            _make(tenant, external_id=312, token=uuid.uuid4(), expires=now - timedelta(days=1)),
            _make(tenant, external_id=313, token=uuid.uuid4(), expires=None),
            _make(
                tenant,
                external_id=314,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
                token=uuid.uuid4(),
                expires=now + timedelta(days=1),
            ),
            _make(tenant, external_id=315, invite_status=CatalogMaster.InviteStatus.CANCELLED),
        ]

        by_query = set(
            CatalogMaster.all_tenants.filter(tenant=tenant)
            .filter(live_invite_q())
            .values_list("pk", flat=True)
        )
        by_predicate = {m.pk for m in rows if has_live_invite(m)}

        assert by_query == by_predicate
        # И это не пустое равенство: живые приглашения среди строк есть.
        assert len(by_predicate) == 2
