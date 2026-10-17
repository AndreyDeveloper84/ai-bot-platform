"""DRF-2542 §1 и §5 — согласие зоны при записи и стирание при его отзыве.

Жёлтых и красных строк сегодня не бывает: заглушка возраста (#597) отбрасывает
любую запись. Узлы подменяют её на «взрослый» — то есть смотрят на день, когда
она уйдёт. Остальное настоящее: писатель, журнал согласий, отзыв, делетер.

Что было (замер на ``d62e07bd``):

* §1 — красная строка записывалась при ``consent_at=now`` без единой записи
  согласия ``memory_red``: писатель верил параметру;
* §5 — отзыв ``memory_red`` снимал согласие, строка оставалась живой.

Узлы:

* w1 — без согласия зоны запись отклоняется и называется строкой аудита;
  с согласием — проходит (близнец);
* w2 — согласие другой зоны и отозванное согласие запись не открывают;
* w3 — сбой чтения согласия закрывает запись;
* w4 — повышение зоны спрашивает тот же журнал;
* w5 — заглушка возраста по-прежнему первая: без неё отказ по возрасту, не по
  согласию;
* d1 — отзыв согласия зоны ставит строкам зоны надгробие ``withdrawal``,
  красным — строку журнала на каждую;
* d2 — отзыв одной зоны не трогает другую и зелёную;
* d3 — согласие держится на человеке: пока оно действует на другой оболочке,
  строки живы; ушло с последней — сняты;
* d4 — сбой стирания отзыв не откатывает;
* d5 — надгробие отзыва физически уходит через сутки, а не через тридцать дней.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import has_memory_consent, withdraw
from apps.identity.models import (
    BotUser,
    MemoryEntry,
    RedZoneAccessLog,
    UserPersonalContext,
)
from apps.identity.services import memory_writer
from apps.identity.services.exceptions import ZonePromotionRequiresConsent
from apps.identity.services.memory_deleter import (
    purge_expired_tombstones,
    soft_delete_zone_for_withdrawal,
)
from apps.identity.services.memory_writer import promote_zone, write_entry
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType
GREEN = MemoryEntry.SENSITIVITY_GREEN
YELLOW = MemoryEntry.SENSITIVITY_YELLOW
RED = MemoryEntry.SENSITIVITY_RED
ZONE_CONSENT = {YELLOW: CT.MEMORY_YELLOW, RED: CT.MEMORY_RED}
NO_CONSENT = RedZoneAccessLog.ACCESS_WRITE_REJECTED_NO_CONSENT


def _adult():
    """Подмена #597: проверка возраста пропускает."""
    return patch.object(memory_writer, "_check_minor_protection", return_value=None)


class _Person:
    def __init__(self, cuid: str, *, consents: tuple[str, ...] = ()) -> None:
        self.tenant, _ = Tenant.objects.get_or_create(
            slug="zone-2542", defaults={"name": "Zone 2542"}
        )
        self.user_id = uuid.uuid4()
        self.upc = UserPersonalContext.objects.create(user_id=self.user_id)
        self.shell = self.add_shell(self.tenant, cuid)
        for consent_type in consents:
            self.grant(consent_type)

    def add_shell(self, tenant: Tenant, cuid: str) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=cuid,
            ayla_user_id=self.user_id,
            customer_status=BotUser.CustomerStatus.LINKED,
        )

    def grant(self, consent_type: str, shell: BotUser | None = None) -> None:
        shell = shell or self.shell
        ConsentRecord.all_tenants.create(
            tenant=shell.tenant,
            bot_user=shell,
            consent_type=consent_type,
            granted=True,
            source="test",
        )

    def withdraw(self, consent_type: str, shell: BotUser | None = None) -> ConsentRecord | None:
        shell = shell or self.shell
        with tenant_scope(shell.tenant):
            return withdraw(shell, consent_type=consent_type, source="test:2542")

    def write(self, zone: str, *, value: str = "probe") -> MemoryEntry | None:
        with _adult():
            return write_entry(
                user_id=self.user_id,
                personal_context=self.upc,
                sensitivity_zone=zone,
                source=MemoryEntry.SOURCE_EXPLICIT,
                kind="contraindication",
                content={"key": "probe", "value": value},
                request_id=uuid.uuid4(),
                purpose="test:2542",
                consent_at=None if zone == GREEN else timezone.now(),
            )

    def rows(self, zone: str, *, live: bool = True) -> int:
        qs = MemoryEntry.objects.filter(user_id=self.user_id, sensitivity_zone=zone)
        if live:
            qs = qs.filter(soft_deleted_at__isnull=True)
        return qs.count()

    def audit(self, access_type: str) -> int:
        return RedZoneAccessLog.objects.filter(
            user_id=self.user_id, access_type=access_type
        ).count()


# ─── §1: согласие зоны при записи ────────────────────────────────────────────


class TestTheWriterAsksTheConsentLedger:
    @pytest.mark.parametrize("zone", [YELLOW, RED])
    def test_w1_without_the_zone_consent_the_write_is_refused_and_named(self, zone: str) -> None:
        person = _Person(f"w1-no-{zone}", consents=(CT.PERSONAL_DATA,))

        assert person.write(zone) is None

        assert person.rows(zone, live=False) == 0
        assert person.audit(NO_CONSENT) == 1

    @pytest.mark.parametrize("zone", [YELLOW, RED])
    def test_w1_with_the_zone_consent_the_write_goes_through(self, zone: str) -> None:
        person = _Person(f"w1-yes-{zone}", consents=(ZONE_CONSENT[zone],))

        entry = person.write(zone)

        assert entry is not None
        assert entry.sensitivity_zone == zone
        assert person.audit(NO_CONSENT) == 0

    def test_w2_the_consent_of_another_zone_does_not_open_this_one(self) -> None:
        person = _Person("w2-other", consents=(CT.MEMORY_YELLOW, CT.MEMORY_GREEN, CT.HEALTH))

        assert person.write(RED) is None
        assert person.write(YELLOW) is not None

    def test_w2_a_withdrawn_consent_does_not_open_the_zone(self) -> None:
        person = _Person("w2-withdrawn", consents=(CT.MEMORY_RED,))
        assert person.withdraw(CT.MEMORY_RED) is not None

        assert person.write(RED) is None
        assert person.audit(NO_CONSENT) == 1

    def test_w3_a_failed_consent_read_closes_the_write(self) -> None:
        person = _Person("w3", consents=(CT.MEMORY_RED,))

        with patch("apps.consent.services.has_memory_consent", side_effect=RuntimeError("down")):
            assert person.write(RED) is None

        assert person.rows(RED, live=False) == 0
        assert person.audit(NO_CONSENT) == 1

    def test_w4_promotion_asks_the_same_ledger(self) -> None:
        person = _Person("w4", consents=(CT.PERSONAL_DATA,))
        green = person.write(GREEN)
        assert green is not None

        with _adult(), pytest.raises(ZonePromotionRequiresConsent):
            promote_zone(
                entry=green,
                new_zone=RED,
                consent_token="token",
                request_id=uuid.uuid4(),
                purpose="test:2542",
            )

        green.refresh_from_db()
        assert green.sensitivity_zone == GREEN
        assert person.audit(NO_CONSENT) == 1

        person.grant(CT.MEMORY_RED)
        with _adult():
            promoted = promote_zone(
                entry=green,
                new_zone=RED,
                consent_token="token",
                request_id=uuid.uuid4(),
                purpose="test:2542",
            )
        assert promoted.sensitivity_zone == RED

    def test_w5_the_age_gate_still_refuses_first(self) -> None:
        person = _Person("w5", consents=(CT.MEMORY_RED,))

        entry = write_entry(
            user_id=person.user_id,
            personal_context=person.upc,
            sensitivity_zone=RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="contraindication",
            content={"key": "probe", "value": "probe"},
            request_id=uuid.uuid4(),
            purpose="test:2542",
            consent_at=timezone.now(),
        )

        assert entry is None
        assert person.audit(RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB) == 1
        assert person.audit(NO_CONSENT) == 0


# ─── §5: отзыв согласия зоны стирает её строки ───────────────────────────────


class TestWithdrawalErasesTheZone:
    def test_d1_withdrawing_the_red_consent_tombstones_red_rows_and_logs_each(self) -> None:
        person = _Person("d1-red", consents=(CT.MEMORY_RED,))
        first, second = person.write(RED, value="a"), person.write(RED, value="b")
        assert first is not None and second is not None

        assert person.withdraw(CT.MEMORY_RED) is not None

        assert person.rows(RED) == 0
        assert person.rows(RED, live=False) == 2
        for row in MemoryEntry.objects.filter(user_id=person.user_id):
            assert row.deletion_reason == "withdrawal"
            assert row.status == "deleted"
            assert row.soft_deleted_at is not None
        logs = RedZoneAccessLog.objects.filter(user_id=person.user_id, access_type="withdrawal")
        assert {log.memory_entry_id for log in logs} == {first.id, second.id}
        assert {log.accessor_role for log in logs} == {"system_job"}

    def test_d1_withdrawing_the_yellow_consent_tombstones_yellow_rows(self) -> None:
        person = _Person("d1-yellow", consents=(CT.MEMORY_YELLOW,))
        assert person.write(YELLOW) is not None

        assert person.withdraw(CT.MEMORY_YELLOW) is not None

        row = MemoryEntry.objects.get(user_id=person.user_id)
        assert row.deletion_reason == "withdrawal"
        assert person.audit("withdrawal") == 0  # журнал доступа — только про красную

    def test_d2_one_zone_withdrawn_leaves_the_others_alone(self) -> None:
        person = _Person("d2", consents=(CT.PERSONAL_DATA, CT.MEMORY_YELLOW, CT.MEMORY_RED))
        assert person.write(GREEN) is not None
        assert person.write(YELLOW) is not None
        assert person.write(RED) is not None

        assert person.withdraw(CT.MEMORY_RED) is not None

        assert (person.rows(GREEN), person.rows(YELLOW), person.rows(RED)) == (1, 1, 0)

    def test_d2_withdrawing_an_unrelated_consent_erases_nothing(self) -> None:
        person = _Person("d2-other", consents=(CT.MARKETING, CT.MEMORY_RED))
        assert person.write(RED) is not None

        assert person.withdraw(CT.MARKETING) is not None

        assert person.rows(RED) == 1

    def test_d3_the_rows_stay_while_another_shell_still_holds_the_consent(self) -> None:
        person = _Person("d3", consents=(CT.MEMORY_RED,))
        other_tenant = Tenant.objects.create(slug="zone-2542-other", name="Other")
        other_shell = person.add_shell(other_tenant, "d3-other")
        person.grant(CT.MEMORY_RED, other_shell)
        assert person.write(RED) is not None

        assert person.withdraw(CT.MEMORY_RED) is not None
        assert has_memory_consent(person.user_id, "red") is True
        assert person.rows(RED) == 1

        assert person.withdraw(CT.MEMORY_RED, other_shell) is not None
        assert has_memory_consent(person.user_id, "red") is False
        assert person.rows(RED) == 0

    def test_d4_a_failed_erase_does_not_roll_the_withdrawal_back(self) -> None:
        person = _Person("d4", consents=(CT.MEMORY_RED,))
        assert person.write(RED) is not None

        with patch(
            "apps.identity.services.memory_deleter.soft_delete_zone_for_withdrawal",
            side_effect=RuntimeError("down"),
        ):
            assert person.withdraw(CT.MEMORY_RED) is not None

        assert has_memory_consent(person.user_id, "red") is False
        assert person.rows(RED) == 1  # строка осталась — и названа в логе, не проглочена

    def test_d5_a_withdrawal_tombstone_is_purged_after_a_day_not_thirty(self) -> None:
        person = _Person("d5", consents=(CT.MEMORY_RED,))
        assert person.write(RED) is not None
        assert person.withdraw(CT.MEMORY_RED) is not None

        early = purge_expired_tombstones(now=timezone.now() + timedelta(hours=23))
        assert early.purged == 0
        late = purge_expired_tombstones(now=timezone.now() + timedelta(hours=25))
        assert late.purged == 1
        assert person.rows(RED, live=False) == 0

    def test_the_deleter_refuses_a_zone_no_zone_consent_governs(self) -> None:
        with pytest.raises(ValueError, match="green"):
            soft_delete_zone_for_withdrawal(uuid.uuid4(), GREEN, request_id=uuid.uuid4())
