"""DRF-2132, шаг 1 — периметр красной зоны закрыт до появления писателя.

Писателя красной зоны нет (заглушка возраста #597 отбрасывает любую запись),
поэтому строки здесь ставятся напрямую в базу — так же, как в соседних узлах
читателя. Предмет — чтение.

Что было: ``RedZoneReader.read`` отдавал строку с надгробием всё время
удержания (до 30 дней после «забудь») и не спрашивал, действует ли согласие.

* g1 — живая строка при действующем согласии читается, журнал пишется под
  ролью читателя (близнец ко всем отказам ниже);
* g2 — надгробие и живая заявка на удаление не читаются никем;
* g3 — без согласия красной зоны, после его отзыва, при «забудь всё» строка
  для использования не отдаётся; журнал чтения не пишется — чтения не было;
* g4 — субъект своё видит и без согласия (экран памяти), но не надгробие;
* g5 — роли ``recommendations`` и ``scanner`` существуют в журнале и получают
  конкретного исполнителя, а не «unknown»;
* g6 — перепись: кто в продукте зовёт читателя и под какой ролью;
* g7 — зелёные читатели и подсказка модели красную строку не несут.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import (
    BotUser,
    MemoryEntry,
    RedZoneAccessLog,
    UserPersonalContext,
)
from apps.identity.services.red_zone_reader import RedZoneReader, RedZoneUseWithheld
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType
CANARY = "CANARY-2132-орехи"
ROLE = RedZoneAccessLog.ACCESSOR_RECOMMENDATIONS
#: ``display`` — строка, которую подсказка модели печатает дословно: красная
#: строка, попавшая к зелёному читателю, была бы в промпте видна.
RED_CONTENT = {"key": "allergy", "value": CANARY, "display": f"аллергия: {CANARY}"}


class _Person:
    def __init__(self, cuid: str, *, consents: tuple[str, ...] = (CT.MEMORY_RED,)) -> None:
        self.tenant, _ = Tenant.objects.get_or_create(
            slug="red-2132", defaults={"name": "Red 2132"}
        )
        self.user_id = uuid.uuid4()
        self.upc = UserPersonalContext.objects.create(user_id=self.user_id)
        self.bot_user = BotUser.all_tenants.create(
            tenant=self.tenant,
            channel="max",
            channel_user_id=cuid,
            ayla_user_id=self.user_id,
            customer_status=BotUser.CustomerStatus.LINKED,
        )
        for consent_type in consents:
            self.grant(consent_type)
        self.entry = MemoryEntry.objects.create(
            user_id=self.user_id,
            personal_context=self.upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            consent_at=timezone.now(),
            kind="contraindication",
            content=dict(RED_CONTENT),
            source_tenant_id=self.tenant.id,
        )

    def grant(self, consent_type: str) -> None:
        ConsentRecord.all_tenants.create(
            tenant=self.tenant,
            bot_user=self.bot_user,
            consent_type=consent_type,
            granted=True,
            source="test",
        )

    def withdraw(self, consent_type: str) -> None:
        ConsentRecord.all_tenants.filter(bot_user=self.bot_user, consent_type=consent_type).update(
            withdrawn_at=timezone.now()
        )

    def read(self, role: str = ROLE, principal: str | None = None) -> MemoryEntry:
        return RedZoneReader.read(
            entry_id=self.entry.id,
            user_id=self.user_id,
            accessor_role=role,
            request_id=uuid.uuid4(),
            purpose="test:2132",
            accessor_principal=principal,
        )

    def read_logs(self) -> int:
        return RedZoneAccessLog.objects.filter(
            memory_entry_id=self.entry.id, access_type=RedZoneAccessLog.ACCESS_READ
        ).count()

    def tombstone(self) -> bool:
        return RedZoneReader.soft_delete_for_subject(
            entry_id=self.entry.id,
            user_id=self.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
            request_id=uuid.uuid4(),
            purpose="test:2132",
            reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
            accessor_principal=f"bot_user:{self.bot_user.pk}",
        )


def test_g1_a_live_row_under_active_consent_is_read_and_logged() -> None:
    person = _Person("g1")

    entry = person.read()

    assert entry.content == RED_CONTENT
    log = RedZoneAccessLog.objects.get(memory_entry_id=person.entry.id)
    assert log.accessor_role == "recommendations"
    assert log.access_type == "read"


class TestOnlyALiveRowIsRead:
    def test_g2_a_tombstoned_row_is_missing_for_a_reader(self) -> None:
        person = _Person("g2-tomb")
        assert person.read().id == person.entry.id  # близнец: до удаления читалась
        assert person.tombstone() is True

        with pytest.raises(MemoryEntry.DoesNotExist):
            person.read()
        assert person.read_logs() == 1  # только чтение до удаления

    def test_g2_a_row_with_a_pending_delete_request_is_missing(self) -> None:
        person = _Person("g2-pending")
        MemoryEntry.objects.filter(pk=person.entry.pk).update(
            delete_requested_at=timezone.now(),
            deletion_reason=MemoryEntry.DELETION_REASON_WITHDRAWAL,
            status=MemoryEntry.STATUS_DELETION_PENDING,
        )

        with pytest.raises(MemoryEntry.DoesNotExist):
            person.read()
        assert person.read_logs() == 0

    def test_g2_a_tombstoned_row_is_missing_for_the_subject_too(self) -> None:
        person = _Person("g2-subject")
        assert person.tombstone() is True

        with pytest.raises(MemoryEntry.DoesNotExist):
            person.read(RedZoneAccessLog.ACCESSOR_DATA_SUBJECT, f"bot_user:{person.bot_user.pk}")


class TestStoredIsNotPermittedToUse:
    def test_g3_without_red_zone_consent_the_row_is_withheld(self) -> None:
        person = _Person("g3-none", consents=())

        with pytest.raises(RedZoneUseWithheld):
            person.read()
        assert person.read_logs() == 0

    def test_g3_another_consent_type_does_not_open_the_red_zone(self) -> None:
        person = _Person("g3-other", consents=(CT.PERSONAL_DATA, CT.HEALTH))

        with pytest.raises(RedZoneUseWithheld):
            person.read()

    def test_g3_a_withdrawn_consent_closes_the_row_at_once(self) -> None:
        person = _Person("g3-withdrawn")
        assert person.read().id == person.entry.id
        person.withdraw(CT.MEMORY_RED)

        with pytest.raises(RedZoneUseWithheld):
            person.read()
        assert person.read_logs() == 1

    def test_g3_forget_all_requested_closes_the_row_before_the_sweep(self) -> None:
        person = _Person("g3-forget")
        UserPersonalContext.objects.filter(pk=person.upc.pk).update(
            forget_all_requested_at=timezone.now()
        )

        with pytest.raises(RedZoneUseWithheld):
            person.read()

    def test_g3_a_reader_that_handles_missing_handles_withheld(self) -> None:
        """Отказ — наследник ``DoesNotExist``: читатель не роняет ход человека."""
        person = _Person("g3-compat", consents=())

        with pytest.raises(MemoryEntry.DoesNotExist):
            person.read()

    @pytest.mark.parametrize(
        "role",
        [
            RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
            RedZoneAccessLog.ACCESSOR_RECOMMENDATIONS,
            RedZoneAccessLog.ACCESSOR_SCANNER,
        ],
    )
    def test_g3_the_gate_holds_for_every_role_that_uses_the_fact(self, role: str) -> None:
        person = _Person(f"g3-{role}", consents=())

        with pytest.raises(RedZoneUseWithheld):
            person.read(role)


def test_g4_the_subject_sees_their_own_row_without_consent() -> None:
    person = _Person("g4", consents=())

    entry = person.read(RedZoneAccessLog.ACCESSOR_DATA_SUBJECT, f"bot_user:{person.bot_user.pk}")

    assert entry.id == person.entry.id
    assert RedZoneAccessLog.objects.get(memory_entry_id=entry.id).accessor_role == "data_subject"


@pytest.mark.parametrize("role", ["recommendations", "scanner"])
def test_g5_the_two_reader_roles_are_loggable_with_a_concrete_principal(role: str) -> None:
    assert role in dict(RedZoneAccessLog.ACCESSOR_ROLE_CHOICES)
    person = _Person(f"g5-{role}")

    person.read(role)

    log = RedZoneAccessLog.objects.get(memory_entry_id=person.entry.id)
    log.full_clean()
    assert log.accessor_role == role
    assert log.accessor_principal.startswith("worker:")
    assert log.accessor_principal != "unknown"


class TestReadersByRole:
    """Перепись: кто в продукте зовёт читателя красной зоны и под какой ролью.

    Лист обещает ровно двух читателей ради использования — фильтр рекомендаций
    и сканер. Сегодня их нет; есть только субъект на экране памяти. Новый
    вызов или новая роль в коде краснит узел — читатель вписывается сюда с
    причиной, а не появляется молча.
    """

    KNOWN_CALLS = {
        ("apps/miniapp_api/views_memory.py", "list_live_for_subject"),
        ("apps/miniapp_api/views_memory.py", "soft_delete_for_subject"),
    }
    #: Роль → файлы продукта, где она названа. Определение ролей и сам
    #: читатель не в счёт.
    KNOWN_ROLE_USERS = {
        "ACCESSOR_DATA_SUBJECT": {"apps/miniapp_api/views_memory.py"},
        "ACCESSOR_SYSTEM_JOB": {
            "apps/identity/services/memory_deleter.py",
            "apps/identity/services/memory_term.py",
            "apps/identity/services/memory_writer.py",
        },
    }
    DEFINITIONS = {"apps/identity/models.py", "apps/identity/services/red_zone_reader.py"}

    def _scan(self) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
        root = Path(__file__).resolve().parents[3]
        calls: set[tuple[str, str]] = set()
        roles: dict[str, set[str]] = {}
        for path in sorted((root / "apps").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            source = path.read_text(encoding="utf-8")
            if "RedZone" not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Attribute):
                    continue
                owner = getattr(node.value, "id", None)
                if owner == "RedZoneReader" and rel not in self.DEFINITIONS:
                    calls.add((rel, node.attr))
                if node.attr.startswith("ACCESSOR_") and rel not in self.DEFINITIONS:
                    roles.setdefault(node.attr, set()).add(rel)
        return calls, roles

    def test_g6_the_reader_is_called_only_from_known_places(self) -> None:
        calls, _ = self._scan()

        assert calls == self.KNOWN_CALLS

    def test_g6_each_role_is_used_only_where_it_is_known(self) -> None:
        _, roles = self._scan()

        assert roles == self.KNOWN_ROLE_USERS


class TestGreenSurfacesCarryNoRedRow:
    def _person_with_a_green_fact(self, cuid: str) -> _Person:
        person = _Person(cuid, consents=(CT.PERSONAL_DATA, CT.MEMORY_RED))
        MemoryEntry.objects.create(
            user_id=person.user_id,
            personal_context=person.upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            kind="lifestyle",
            content={"key": "diet", "value": "vegan"},
        )
        return person

    def test_g7_green_readers_return_the_green_fact_and_not_the_red_one(self) -> None:
        from apps.identity.services.memory_key_policy import read_current_view
        from apps.identity.services.memory_reader import read_personal_context

        person = self._person_with_a_green_fact("g7-readers")

        for view in (read_personal_context(person.user_id), read_current_view(person.user_id)):
            assert [f.content for f in view.green_facts] == [{"key": "diet", "value": "vegan"}]

    def test_g7_the_prompt_block_does_not_carry_the_red_row(self) -> None:
        from apps.persona.memory_surface import render_current_personal_context

        person = self._person_with_a_green_fact("g7-prompt")

        block = render_current_personal_context(person.bot_user)

        assert block is not None  # близнец: зелёный факт в подсказке есть
        assert CANARY not in block
        assert "аллерг" not in block.lower()
