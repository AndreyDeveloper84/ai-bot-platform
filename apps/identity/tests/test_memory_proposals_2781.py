"""DRF-2781 — умная память Ф4a-3: предположение показывается, подтверждается, исправляется.

Решение владельца 05.10.2026: «Предположение НЕ становится фактом и НЕ
ограничивает выбор клиента без подтверждения»; раздел памяти — «Вы сообщили»
и «Ayla предлагает запомнить»; действия — подтвердить / исправить / удалить.

* g1 — гейт разговора: неподтверждённое не идёт в промпт консьержа и в
  «текущий вид»; подтверждённое идёт (красный до правки — шло);
* g2 — неподтверждённое того же ключа не вытесняет сказанное из разговора;
* g3 — просроченное подтверждённое (свип DRF-2782) из разговора уходит;
* g4 — после отзыва согласия на предположения подтверждённое из разговора
  уходит сразу, сказанное остаётся;
* s1 — экран видит обе группы: сказанное и предложение того же ключа;
* c1 — подтверждение: та же строка, ``user_confirmed_inference``, 180 дней;
  повторное — из ``reconfirm`` обратно в ``confirmed``;
* c2 — без согласия на предположения подтверждение отказано;
* c3 — подтверждать нечего у сказанного и чужого;
* r1 — исправление: новая сказанная строка, предложение вытеснено как
  ``corrected``; исправлять можно только неподтверждённое;
* d1 — «удалить» на предложении не стирает сказанное того же ключа.

Данные синтетические.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_key_policy import read_current_view
from apps.identity.services.memory_proposals import (
    ProposalError,
    confirm_proposal,
    correct_proposal,
    fact_state,
)
from apps.identity.services.memory_writer import write_entry

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
PI = ConsentRecord.ConsentType.PREFERENCE_INFERENCE.value


def _person(uid: str, *, inference: bool = True) -> tuple[BotUser, uuid.UUID]:
    user_id = uuid.uuid4()
    UserPersonalContext.objects.create(user_id=user_id)
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, ayla_user_id=user_id
    )
    record_global_consent(bot_user, consent_type=PD, source="t", document_version="v1")
    if inference:
        record_global_consent(bot_user, consent_type=PI, source="t", document_version="v1")
    return bot_user, user_id


def _write(user_id: uuid.UUID, source: str, value: str, key: str = "visit_time") -> MemoryEntry:
    entry = write_entry(
        user_id=user_id,
        personal_context=UserPersonalContext.objects.get(user_id=user_id),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        kind="preference",
        content={"key": key, "value": value},
        request_id=uuid.uuid4(),
        purpose="test:2781",
        last_inferred_at=None if source == MemoryEntry.SOURCE_EXPLICIT else timezone.now(),
    )
    assert entry is not None
    return entry


def _surfaced(user_id: uuid.UUID) -> list[str]:
    return [f.content.get("value") for f in read_current_view(user_id).green_facts]


# --------------------------------------------------------------------- #
# g — гейт разговора
# --------------------------------------------------------------------- #
def test_g1_an_unconfirmed_inference_never_reaches_the_conversation() -> None:
    bot_user, user_id = _person("2781101")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")
    assert fact_state(proposal) == "proposed"
    # Строка живая и читается — пусто ниже значит «отфильтровано», не «нечего читать».
    from apps.identity.services.memory_reader import read_green_entries

    assert [e.id for e in read_green_entries(user_id)] == [proposal.id]

    assert _surfaced(user_id) == []

    confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)

    assert _surfaced(user_id) == ["after_18"]


def test_g2_a_proposal_does_not_displace_what_the_person_said() -> None:
    _, user_id = _person("2781201")
    _write(user_id, MemoryEntry.SOURCE_EXPLICIT, "morning")
    _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    assert _surfaced(user_id) == ["morning"]


def test_g3_an_expired_confirmation_leaves_the_conversation() -> None:
    bot_user, user_id = _person("2781301")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")
    confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)
    assert _surfaced(user_id) == ["after_18"]

    # Так свип DRF-2782 помечает истёкшее подтверждённое — «переподтвердить».
    MemoryEntry.objects.filter(pk=proposal.pk).update(status=MemoryEntry.STATUS_EXPIRED)

    assert _surfaced(user_id) == []
    proposal.refresh_from_db()
    assert fact_state(proposal) == "reconfirm"


def test_g4_withdrawing_the_inference_consent_stops_using_confirmed_ones_at_once() -> None:
    from apps.consent.preference_inference import withdraw

    bot_user, user_id = _person("2781351")
    _write(user_id, MemoryEntry.SOURCE_EXPLICIT, "vegan", key="diet")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")
    confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)
    assert sorted(_surfaced(user_id)) == ["after_18", "vegan"]

    withdraw(bot_user)

    assert _surfaced(user_id) == ["vegan"]


# --------------------------------------------------------------------- #
# s — экран
# --------------------------------------------------------------------- #
def test_s1_the_screen_shows_the_said_value_and_the_proposal_of_the_same_key() -> None:
    from apps.identity.services.memory_reader import read_green_entries
    from apps.miniapp_api.views_memory import _screen_facts

    _, user_id = _person("2781401")
    _write(user_id, MemoryEntry.SOURCE_EXPLICIT, "morning")
    _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    shown = {
        (fact_state(e), e.content["value"]) for e in _screen_facts(read_green_entries(user_id))
    }

    assert shown == {("said", "morning"), ("proposed", "after_18")}


# --------------------------------------------------------------------- #
# c — подтверждение
# --------------------------------------------------------------------- #
def test_c1_confirming_marks_the_same_row_for_180_days_and_reconfirm_renews_it() -> None:
    bot_user, user_id = _person("2781501")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    before = timezone.now()
    confirmed = confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)

    assert confirmed.pk == proposal.pk
    assert confirmed.source == MemoryEntry.SOURCE_INFERRED
    assert confirmed.provenance == MemoryEntry.PROVENANCE_USER_CONFIRMED_INFERENCE
    assert confirmed.status == MemoryEntry.STATUS_ACTIVE
    assert confirmed.updated_at is not None and confirmed.updated_at >= before
    assert confirmed.expires_at == confirmed.updated_at + timedelta(days=180)

    MemoryEntry.objects.filter(pk=proposal.pk).update(status=MemoryEntry.STATUS_EXPIRED)
    renewed = confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)

    assert renewed.status == MemoryEntry.STATUS_ACTIVE
    assert fact_state(renewed) == "confirmed"
    assert renewed.expires_at == renewed.updated_at + timedelta(days=180)


def test_c2_without_the_inference_consent_confirming_is_refused() -> None:
    bot_user, user_id = _person("2781601", inference=False)
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    with pytest.raises(ProposalError) as refused:
        confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=proposal.id)

    assert refused.value.code == "consent_required"
    proposal.refresh_from_db()
    assert proposal.provenance is None


def test_c3_there_is_nothing_to_confirm_on_a_said_fact_or_a_stranger() -> None:
    bot_user, user_id = _person("2781701")
    said = _write(user_id, MemoryEntry.SOURCE_EXPLICIT, "morning")
    _, stranger_id = _person("2781702")
    foreign = _write(stranger_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    for entry_id in (said.id, foreign.id):
        with pytest.raises(ProposalError) as refused:
            confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=entry_id)
        assert refused.value.code == "not_found"
    foreign.refresh_from_db()
    assert foreign.provenance is None


# --------------------------------------------------------------------- #
# r — исправление
# --------------------------------------------------------------------- #
def test_r1_correcting_writes_a_said_fact_and_supersedes_the_proposal() -> None:
    bot_user, user_id = _person("2781801")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    said = correct_proposal(
        bot_user=bot_user, user_id=user_id, entry_id=proposal.id, value=" утро "
    )

    assert said.source == MemoryEntry.SOURCE_EXPLICIT
    assert said.provenance == MemoryEntry.PROVENANCE_USER_STATED
    assert said.content == {"key": "visit_time", "value": "утро"}
    proposal.refresh_from_db()
    assert proposal.status == MemoryEntry.STATUS_SUPERSEDED
    assert proposal.supersession_reason == MemoryEntry.SUPERSESSION_CORRECTED
    assert proposal.superseded_by_id == said.pk
    assert _surfaced(user_id) == ["утро"]

    # Подтверждённое не исправляют — его удаляют или подтверждают заново.
    second = _write(user_id, MemoryEntry.SOURCE_INFERRED, "weekend", key="visit_day")
    confirm_proposal(bot_user=bot_user, user_id=user_id, entry_id=second.id)
    with pytest.raises(ProposalError) as refused:
        correct_proposal(bot_user=bot_user, user_id=user_id, entry_id=second.id, value="будни")
    assert refused.value.code == "not_a_proposal"


# --------------------------------------------------------------------- #
# d — удаление
# --------------------------------------------------------------------- #
def test_d1_deleting_a_proposal_keeps_the_said_fact_of_the_same_key() -> None:
    from apps.miniapp_api.views_memory import _green_ids_to_forget

    _, user_id = _person("2781901")
    said = _write(user_id, MemoryEntry.SOURCE_EXPLICIT, "morning")
    proposal = _write(user_id, MemoryEntry.SOURCE_INFERRED, "after_18")

    assert _green_ids_to_forget(user_id, proposal.id) == [proposal.id]
    # Положительный контроль: «удалить» на сказанном по-прежнему снимает весь ключ.
    assert set(_green_ids_to_forget(user_id, said.id)) == {said.id, proposal.id}
