"""E0#1 Variant A — `_load_tenant_master_roster` tests (founder verdict
2026-06-02).

Covers the DB-touching half of pre-injection: loading active
``CatalogMaster`` rows for one tenant + capping + tenant isolation +
graceful failure. The pure rendering half is tested in
`test_prompts.py::TestKnownMastersBlock`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Final
from unittest.mock import patch

import pytest

from apps.catalog.master_state import AVAILABLE
from apps.catalog.models import CatalogMaster
from apps.skills.booking.skill import (
    _KNOWN_MASTERS_ROSTER_CAP,
    _load_tenant_master_roster,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="km-roster", name="KM Roster")


@pytest.fixture
def tenant_other() -> Tenant:
    return Tenant.objects.create(slug="km-roster-other", name="KM Other")


_next_external_id = [1]


#: Sentinel for ``_master(ayla_user_id=…)`` — mint a fresh canonical link.
#:
#: DRF-1544. A row the customer can be told about always carries one in
#: production (catalog sync writes ``dto.user_id``), so this is the realistic
#: default; ``None`` has to be asked for, because it means «off the roster,
#: because the booking notification would never reach her» (DRF-1540).
AUTO_AYLA_LINK: Final = "auto"


def _master(
    tenant: Tenant,
    *,
    name: str,
    specialization: str = "",
    is_active: bool = True,
    invite_status: str = CatalogMaster.InviteStatus.ACCEPTED,
    archived_at: datetime | None = None,
    ayla_user_id: uuid.UUID | None | str = AUTO_AYLA_LINK,
) -> CatalogMaster:
    # external_id is part of CatalogMaster's unique constraint
    # (tenant, external_id) — auto-increment for test convenience.
    _next_external_id[0] += 1
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=_next_external_id[0],
        external_updated_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        name=name,
        specialization=specialization,
        is_active=is_active,
        invite_status=invite_status,
        archived_at=archived_at,
        ayla_user_id=(uuid.uuid4() if ayla_user_id == AUTO_AYLA_LINK else ayla_user_id),
    )


class TestLoadRoster:
    def test_empty_tenant_returns_empty_tuple(self, tenant: Tenant) -> None:
        # New tenant с no synced masters yet — block must be skipped
        # gracefully (show_masters tool fallback remains).
        assert _load_tenant_master_roster(tenant) == ([], False)

    def test_returns_active_accepted_masters_with_specialization(self, tenant: Tenant) -> None:
        _master(tenant, name="Ольга", specialization="массаж")
        _master(tenant, name="Анна", specialization="маникюр")

        roster, is_truncated = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Ольга", "Анна"}
        specs = {row["specialization"] for row in roster}
        assert specs == {"массаж", "маникюр"}
        assert is_truncated is False

    def test_inactive_masters_excluded(self, tenant: Tenant) -> None:
        _master(tenant, name="Активный", specialization="x", is_active=True)
        _master(tenant, name="Архивный", specialization="y", is_active=False)

        roster, _ = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Активный"}
        assert "Архивный" not in names

    def test_pending_invite_masters_excluded(self, tenant: Tenant) -> None:
        # Adversarial CR F2 — `is_active=True, invite_status=PENDING`
        # is an M0-flow row that should NOT surface as a bookable
        # master. Pre-fix this leaked into the roster.
        _master(
            tenant,
            name="Маша-в-приглашении",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        _master(
            tenant,
            name="Принятая",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        roster, _ = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Принятая"}
        assert "Маша-в-приглашении" not in names

    def test_expired_and_cancelled_invite_masters_excluded(self, tenant: Tenant) -> None:
        # F2 belt-and-braces — non-ACCEPTED states are uniformly excluded.
        _master(
            tenant,
            name="Истёкший",
            invite_status=CatalogMaster.InviteStatus.EXPIRED,
        )
        _master(
            tenant,
            name="Отменённый",
            invite_status=CatalogMaster.InviteStatus.CANCELLED,
        )
        _master(
            tenant,
            name="Активный",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        roster, _ = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Активный"}

    def test_sorted_alphabetically_for_stability(self, tenant: Tenant) -> None:
        _master(tenant, name="Светлана")
        _master(tenant, name="Анна")
        _master(tenant, name="Ольга")

        roster, _ = _load_tenant_master_roster(tenant)
        names = [row["name"] for row in roster]
        # Order matters — the prompt token shape must be deterministic
        # so prompt-cache hits stay high across turns.
        assert names == sorted(names)

    def test_caps_at_roster_limit_and_flags_truncated(self, tenant: Tenant) -> None:
        # Adversarial CR F3 — create cap+5 active masters; loader
        # returns exactly cap rows AND raises the is_truncated flag.
        for i in range(_KNOWN_MASTERS_ROSTER_CAP + 5):
            # Pad zero so the lex order matches the index order (10 < 9
            # would otherwise sort wrong as plain strings).
            _master(tenant, name=f"Master-{i:03d}", specialization="x")

        roster, is_truncated = _load_tenant_master_roster(tenant)
        assert len(roster) == _KNOWN_MASTERS_ROSTER_CAP
        assert is_truncated is True
        # First should be Master-000, last should be the cap-1.
        assert roster[0]["name"] == "Master-000"
        assert roster[-1]["name"] == f"Master-{_KNOWN_MASTERS_ROSTER_CAP - 1:03d}"

    def test_exactly_at_cap_does_not_flag_truncated(self, tenant: Tenant) -> None:
        # F3 boundary — when the roster size equals the cap exactly,
        # no overflow signal because the +1 probe found nothing extra.
        for i in range(_KNOWN_MASTERS_ROSTER_CAP):
            _master(tenant, name=f"Master-{i:03d}")
        roster, is_truncated = _load_tenant_master_roster(tenant)
        assert len(roster) == _KNOWN_MASTERS_ROSTER_CAP
        assert is_truncated is False

    def test_cross_tenant_isolation(self, tenant: Tenant, tenant_other: Tenant) -> None:
        _master(tenant, name="Тенант-А", specialization="x")
        _master(tenant_other, name="Тенант-Б", specialization="y")

        roster_a, _ = _load_tenant_master_roster(tenant)
        names_a = {row["name"] for row in roster_a}
        assert names_a == {"Тенант-А"}
        # Critical adversarial concern — tenant boundary must hold.
        assert "Тенант-Б" not in names_a

    def test_db_failure_returns_empty_tuple_does_not_raise(self, tenant: Tenant) -> None:
        # Loader MUST NOT propagate DB exceptions — observability /
        # anti-hallucination block is defence-in-depth, not load-bearing.
        # A turn must complete even when the catalog mirror is briefly
        # unavailable.
        with patch(
            "apps.catalog.models.CatalogMaster.all_tenants",
        ) as mock_mgr:
            mock_mgr.filter.side_effect = RuntimeError("synthetic db outage")
            roster, is_truncated = _load_tenant_master_roster(tenant)
            assert roster == []
            assert is_truncated is False

    def test_empty_string_specialization_preserved(self, tenant: Tenant) -> None:
        # Legacy mysite-synced rows may have specialization="" — loader
        # surfaces empty string (renderer drops the «— spec» suffix).
        _master(tenant, name="Без специализации", specialization="")
        roster, _ = _load_tenant_master_roster(tenant)
        assert len(roster) == 1
        assert roster[0]["specialization"] == ""


class TestTheRosterAsksTheSaleGate:
    """DRF-1544 — ростер в промпте спрашивает ``AVAILABLE``, а не свою копию.

    Здесь ставка выше, чем на витрине: имя в этом списке — это имя,
    которое ассистент подтвердит как записываемое («такого мастера нет»
    он говорит ровно про тех, кого в списке не увидел). Мастер, которого
    гейт продажи не пропускает, не имеет права доехать до промпта —
    иначе модель предложит человека, к которому запись не дойдёт.

    Решение владельца 06.09.2026 — реестр открытых решений, §32, пункт 1
    (документ живёт вне этого репозитория).
    """

    def test_an_ordinary_bookable_master_is_still_in_the_prompt(self, tenant: Tenant) -> None:
        """Положительная стража. Стоит первой: два отрицания ниже верны
        и на пустом ростере, а пустой ростер — это отключённый блок."""
        _master(tenant, name="Сазонова Инна", specialization="массаж")

        roster, is_truncated = _load_tenant_master_roster(tenant)

        assert [row["name"] for row in roster] == ["Сазонова Инна"]
        assert is_truncated is False

    def test_a_master_without_an_ayla_link_never_reaches_the_prompt(self, tenant: Tenant) -> None:
        _master(tenant, name="Сазонова Инна")
        _master(tenant, name="Без связи с Ayla", ayla_user_id=None)

        roster, _ = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}

        assert "Сазонова Инна" in names  # присутствие: ростер не пуст
        assert "Без связи с Ayla" not in names

    def test_an_archived_master_never_reaches_the_prompt(self, tenant: Tenant) -> None:
        """Латентный пропуск ручного набора: ``archived_at`` он не спрашивал."""
        _master(tenant, name="Сазонова Инна")
        _master(
            tenant,
            name="В архиве",
            archived_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        )

        roster, _ = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}

        assert "Сазонова Инна" in names  # присутствие: ростер не пуст
        assert "В архиве" not in names

    def test_the_pilot_shape_loses_nobody(self, tenant: Tenant) -> None:
        """Замер: 31 бронируемый, все со связью — ростер обязан не сузиться.

        Форма боевого контура 06.09.2026. Считаются мастера, а не строки
        промпта: сузься список — ассистент начал бы отрицать людей,
        которые на самом деле принимают.

        Отсечка ``_KNOWN_MASTERS_ROSTER_CAP`` (20) ниже пилотных 31, и это
        не мешает замеру, а задаёт его форму: сравнивать надо ТО, ЧТО
        ПОВЕРХНОСТЬ ОТДАЁТ, до и после — иначе замер померил бы отсечку по
        размеру промпта, а не гейт. Прежний набор на этих данных отдал бы
        те же двадцать имён и тот же сигнал усечения; их и сверяем.
        """
        pilot_bookable = 31
        for index in range(pilot_bookable):
            _master(tenant, name=f"Мастер {index:02d}")

        # Что отдала бы поверхность ДО перевода — ручной набор, порядок и
        # отсечка те же, что в самой функции.
        hand_rolled = list(
            CatalogMaster.all_tenants.filter(
                tenant=tenant,
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )
            .order_by("name")
            .values_list("name", flat=True)[: _KNOWN_MASTERS_ROSTER_CAP + 1]
        )
        before = list(hand_rolled[:_KNOWN_MASTERS_ROSTER_CAP])
        before_truncated = len(hand_rolled) > _KNOWN_MASTERS_ROSTER_CAP

        roster, is_truncated = _load_tenant_master_roster(tenant)
        after = [row["name"] for row in roster]

        # Присутствие раньше отрицания: данные действительно заведены и
        # отсечка действительно сработала, иначе равенство ниже сравнивало
        # бы два пустых списка.
        assert len(before) == _KNOWN_MASTERS_ROSTER_CAP
        assert before_truncated is True

        assert after == before
        assert is_truncated == before_truncated

        # И ни один из 31 не потерян по причине гейта: усечение — это
        # отсечка промпта, а не отказ в продаже.
        assert (
            CatalogMaster.all_tenants.filter(tenant=tenant).filter(AVAILABLE).count()
            == pilot_bookable
        )
