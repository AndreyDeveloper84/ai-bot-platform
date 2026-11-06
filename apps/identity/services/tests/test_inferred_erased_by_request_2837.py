"""DRF-2837 — писатель выводов не предлагает снова то, что человек просил забыть.

Дедуп писателя строится по **живым** строкам. Отклонённое предложение и
стёртый сказанный факт лежат надгробием, и до этого листа следующий прогон
записал бы их заново — без единого нового слова человека.

Все узлы — на настоящих надгробиях: строку ставит писатель, снимает путь
удаления, очищает свип. Ничего не подставлено руками, кроме дат «прошло N дней».

* e1 — отклонённое на экране предложение не возвращается;
* e2 — стёртое в чате СКАЗАННОЕ писатель выводов тоже не предлагает;
* e3 — человек говорит то же снова — пишется: запрет стоит на двери выводов,
  а не в хранилище;
* e4 — запрет опознаёт факт, а не род: другое значение того же ключа пишется;
* e5 — срок хранения — не просьба: ``ttl_purge`` не блокирует;
* e6 — после физической очистки надгробия запрета нет: помнить нечем;
* e7 — чужое надгробие не блокирует;
* e8 — исправленное человеком предложение не возвращается (вытесненная строка
  живая, её видит прежний дедуп — узел держит это, чтобы не разошлось молча);
* e9 — дверь одна: вывод в память пишет только ``memory_inferred``.
"""

from __future__ import annotations

import ast
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser, MemoryEntry
from apps.identity.services.memory_deleter import (
    TOMBSTONE_RETENTION,
    purge_expired_tombstones,
    soft_delete_green_entries,
)
from apps.identity.services.memory_inferred import (
    InferredGreenFact,
    record_inferred_green_facts,
)
from apps.identity.services.memory_reader import (
    get_or_create_personal_context,
    read_green_entries,
)
from apps.identity.services.memory_writer import write_entry
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType

KIND = "preference"
KEY = "visit_time"
VALUE = "after_18"


@pytest.fixture
def person(db) -> BotUser:
    tenant = Tenant.objects.create(slug="inf-2837", name="Inf 2837")
    return _person(tenant, "p1")


def _person(tenant: Tenant, cuid: str) -> BotUser:
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=cuid,
        ayla_user_id=uuid.uuid4(),
        customer_status=BotUser.CustomerStatus.LINKED,
    )
    for consent_type in (CT.PERSONAL_DATA, CT.PREFERENCE_INFERENCE):
        ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            consent_type=consent_type,
            granted=True,
            source="test",
        )
    return bot_user


def _uid(person: BotUser) -> uuid.UUID:
    assert person.ayla_user_id is not None
    return person.ayla_user_id


def _propose(person: BotUser, value: str = VALUE) -> int:
    return record_inferred_green_facts(
        person, [InferredGreenFact(kind=KIND, content={"key": KEY, "value": value})]
    )


def _say(person: BotUser, value: str = VALUE) -> MemoryEntry | None:
    return write_entry(
        user_id=_uid(person),
        personal_context=get_or_create_personal_context(_uid(person)),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind=KIND,
        content={"key": KEY, "value": value},
        request_id=uuid.uuid4(),
        purpose="test:2837",
    )


def _erase(person: BotUser, reason: str) -> int:
    rows = read_green_entries(_uid(person))
    return soft_delete_green_entries(_uid(person), [r.id for r in rows], reason=reason)


def _live(person: BotUser) -> list[MemoryEntry]:
    return read_green_entries(_uid(person))


def test_e1_a_proposal_rejected_on_the_screen_is_not_proposed_again(person) -> None:
    # Положительная стража: предложение БЫЛО и стёрто — иначе «не вернулось»
    # зеленело бы на пустоте.
    assert _propose(person) == 1
    assert _erase(person, MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP) == 1

    assert _propose(person) == 0
    assert _live(person) == []


def test_e2_a_said_fact_erased_in_chat_is_not_inferred_back(person) -> None:
    assert _say(person) is not None
    assert _erase(person, MemoryEntry.DELETION_REASON_USER_DELETE) == 1

    assert _propose(person) == 0
    assert _live(person) == []


def test_e3_saying_it_again_is_the_persons_right(person) -> None:
    assert _propose(person) == 1
    assert _erase(person, MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP) == 1
    assert _propose(person) == 0

    assert _say(person) is not None

    (row,) = _live(person)
    assert row.source == MemoryEntry.SOURCE_EXPLICIT
    assert row.content == {"key": KEY, "value": VALUE}


def test_e4_the_block_names_one_fact_not_its_whole_kind(person) -> None:
    assert _propose(person) == 1
    assert _erase(person, MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP) == 1

    assert _propose(person, value="before_12") == 1

    (row,) = _live(person)
    assert row.content == {"key": KEY, "value": "before_12"}


def test_e5_an_expired_term_is_not_a_request(person) -> None:
    assert _propose(person) == 1
    assert _erase(person, MemoryEntry.DELETION_REASON_TTL_PURGE) == 1

    assert _propose(person) == 1


def test_e6_after_the_tombstone_is_purged_nothing_is_remembered(person) -> None:
    assert _propose(person) == 1
    assert _erase(person, MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP) == 1
    assert _propose(person) == 0

    later = timezone.now() + TOMBSTONE_RETENTION + timedelta(days=1)
    assert purge_expired_tombstones(now=later).purged == 1
    assert MemoryEntry.objects.filter(user_id=_uid(person)).count() == 0

    assert _propose(person) == 1


def test_e7_another_persons_tombstone_blocks_nobody_else(person) -> None:
    other = _person(person.tenant, "p2")
    assert _propose(other) == 1
    assert _erase(other, MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP) == 1

    assert _propose(person) == 1


def test_e8_a_proposal_the_person_corrected_is_not_proposed_again(person) -> None:
    from apps.identity.services.memory_proposals import correct_proposal

    assert _propose(person) == 1
    (proposal,) = _live(person)
    correct_proposal(
        bot_user=person,
        user_id=_uid(person),
        entry_id=proposal.id,
        value="before_12",
    )

    assert _propose(person) == 0


class TestOneDoor:
    """Запрет стоит в ``record_inferred_green_facts``. Он защищает, пока вывод
    нельзя записать мимо неё — поэтому второй писатель выводов краснит узел."""

    DOOR = "apps/identity/services/memory_inferred.py"

    def _writers_of_inferences(self) -> list[str]:
        root = Path(__file__).resolve().parents[4]
        found: list[str] = []
        for path in sorted((root / "apps").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            source = path.read_text(encoding="utf-8")
            if "write_entry" not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name != "write_entry":
                    continue
                given = next((k.value for k in node.keywords if k.arg == "source"), None)
                # Всё, что не литерально «сказано», считается выводом: источник
                # из переменной тоже может оказаться ``inferred``.
                if getattr(given, "attr", None) != "SOURCE_EXPLICIT":
                    found.append(rel)
        return found

    def test_e9_only_the_guarded_door_writes_an_inference(self) -> None:
        assert self._writers_of_inferences() == [self.DOOR]
