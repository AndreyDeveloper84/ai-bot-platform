"""The safety verdict rides in the state, and a state written before it existed is `UNKNOWN`.

### The one failure these tests exist for

A `ConversationState` blob written before this field existed has no `"safety"`
key. Every one of those blobs is sitting in Redis right now. If `_decode`
answered `NORMAL` for a missing key, the fail-closed construction would
collapse **silently, on data written yesterday** — and every check in this
package would stay green, because nothing here asks a decoder what it invents.

`SafetyResult` has no default for `state` for exactly that reason: the Safety
Engine may only answer `NORMAL` when it actually ran. The decoder is a second
door into the same type, and the prohibition applies to it whole.

### Why a default of `UNKNOWN` is not the same thing

`ConversationState.safety` *does* have a default. That is not a contradiction:

    NORMAL as a default   grants a permission nobody gave
    UNKNOWN as a default  grants nothing — `BLOCKED(SAFETY_UNKNOWN)`

A default is admissible exactly when forgetting to set it withholds rather than
permits. Both directions are checked below.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.safety_input import (
    Handoff,
    SafetyResult,
    SafetyState,
)
from apps.orchestrator.decision_readiness.state import (
    ConversationState,
    SlotState,
    SlotValue,
    StateLifecycle,
)
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _verdict(revision: int = 3) -> SafetyResult:
    return SafetyResult(
        state=SafetyState.CLARIFY,
        evaluated_at_revision=revision,
        # §127: известное состояние обязано нести обещание. Конструктор не
        # соберётся без него — и это правильно, поэтому здесь оно названо, а
        # не подставлено умолчанием.
        handoff=Handoff.RECOMMENDED,
        rule_id="pre_check.regex:pregnancy",
        policy_version="safety-2026-09-01",
        required_slots=("trimester",),
        forbidden_capabilities=("booking.create",),
    )


def _unreadable_blob() -> str:
    return json.dumps({"v": 2, "conversation_id": "c", "revision": 1, "slots": {}, "safety": 7})


def _state(conversation_id: str = "conv-safety") -> ConversationState:
    revision = state_mod.next_revision(conversation_id)
    return ConversationState(
        conversation_id=conversation_id,
        revision=revision,
        slots={"city": SlotValue(state=SlotState.KNOWN, value="Пенза", at_revision=revision)},
        epoch_started_at_revision=revision,
    )


# ─── the missing key ────────────────────────────────────────────────────────


def test_a_state_written_before_the_field_reads_as_unknown(fake_redis: FakeRedis) -> None:
    """A v1 blob — no `"safety"` key at all — must decode to `UNKNOWN`.

    Written by hand rather than by dropping the key from `_encode` output: the
    subject is the shape that is **already in Redis**, and building it from
    today's encoder would let a change to the encoder quietly change what this
    test is about.
    """
    blob = json.dumps(
        {
            "v": 1,
            "conversation_id": "conv-old",
            "revision": 4,
            "epoch_started_at_revision": 1,
            "last_activity_at": None,
            "slots": {"city": {"state": "known", "value": "Пенза", "at_revision": 2}},
        },
        ensure_ascii=False,
    )

    decoded = state_mod._decode(blob)

    # Сначала — что блоб вообще прочитан. Разбор, упавший на полпути, не
    # должен читаться как «состояние неизвестно».
    assert decoded.conversation_id == "conv-old"
    assert decoded.slot("city").value == "Пенза"
    # И предмет.
    assert decoded.safety.state is SafetyState.UNKNOWN
    assert decoded.safety.is_known is False


def test_the_missing_key_is_not_read_as_normal(fake_redis: FakeRedis) -> None:
    """The same fact stated as the prohibition it comes from.

    Kept separate from the test above because the two fail for different
    reasons and a reader deserves to see which. `UNKNOWN` is right; `NORMAL` is
    the specific wrong answer §11.4 names, and it is wrong even though it is
    the most common verdict in production.
    """
    blob = json.dumps({"v": 1, "conversation_id": "c", "revision": 1, "slots": {}})

    decoded = state_mod._decode(blob)

    assert decoded.safety.state is not SafetyState.NORMAL


def test_an_unreadable_safety_entry_is_unknown_rather_than_a_crash(fake_redis: FakeRedis) -> None:
    """A key holding something that is not an object is also "we were not told".

    Tolerance about **shape**, and it has an address: during a rolling deploy an
    older reader meets a newer writer, and killing the person's turn over that
    buys nothing a block does not already give. What was lost is the verdict,
    and the honest reading of a lost verdict is that we were not told.

    Strictness about **value** is the next test, and the two are not in tension:
    a format may outlive its readers, but it may not hold a fact it has a
    shorter way of not holding.
    """
    blob = json.dumps({"v": 2, "conversation_id": "c", "revision": 1, "slots": {}, "safety": "x"})

    decoded = state_mod._decode(blob)

    assert decoded.safety.state is SafetyState.UNKNOWN


# ─── «не оценивали» has exactly one representation ──────────────────────────


def test_an_absent_verdict_is_written_as_no_key_at_all() -> None:
    """Not `null`, not an object saying `UNKNOWN` — the key is simply absent.

    One representation per fact. The moment a second one exists, `UNKNOWN` in
    storage stops meaning «the engine did not run» and starts meaning that *or*
    «a writer had nothing and wrote it anyway» — and nothing downstream can
    tell which.
    """
    encoded = json.loads(state_mod._encode(ConversationState(conversation_id="c", revision=1)))

    # Сначала — что блоб вообще собран тем кодеком, который проверяем.
    assert encoded["conversation_id"] == "c"
    assert "safety" not in encoded


def test_a_stored_unknown_is_refused_rather_than_read(fake_redis: FakeRedis) -> None:
    """The dangerous half, and it is dangerous *because* it is fail-closed.

    A wrong `NORMAL` gets noticed: it lets through something that should have
    stopped. A wrong `UNKNOWN` looks like caution, blocks for a plausible
    reason, and nobody ever asks which reason — the mechanism would block
    correctly nine times and on a swallowed error the tenth, and from outside
    those are the same event.

    `SafetyResult.__post_init__` cannot catch this, and not from weakness:
    `UNKNOWN` with no revision is its permitted pair. Origin is not visible from
    the value, so the storage format has to be the thing that keeps it — by
    having no way to say it.
    """
    blob = json.dumps(
        {
            "v": 2,
            "conversation_id": "c",
            "revision": 1,
            "slots": {},
            "safety": {"state": "unknown", "evaluated_at_revision": None},
        }
    )

    with pytest.raises(state_mod.StoredVerdictWithoutOrigin):
        state_mod._decode(blob)


def test_an_absent_verdict_round_trips_through_redis(fake_redis: FakeRedis) -> None:
    """Положительный контроль к двум предыдущим.

    Без него они зеленели бы на кодеке, который не пишет вердикт **никогда** —
    и «ключа нет» означало бы не «не оценивали», а «эта запись сломана».
    """
    state_mod.save(_state("conv-absent"))

    result = state_mod.load("conv-absent")

    assert result.lifecycle is StateLifecycle.LIVE
    assert result.state is not None
    assert result.state.safety.state is SafetyState.UNKNOWN


# ─── the verdict survives the round trip whole ──────────────────────────────


def test_every_field_of_the_verdict_survives_redis(fake_redis: FakeRedis) -> None:
    saved = _state().with_safety(_verdict(revision=3))
    state_mod.save(saved)

    result = state_mod.load(saved.conversation_id)

    assert result.lifecycle is StateLifecycle.LIVE
    assert result.state is not None
    assert result.state.safety == saved.safety


def test_the_default_verdict_is_unknown_not_normal() -> None:
    """Forgetting to set it must withhold, never permit."""

    state = ConversationState(conversation_id="c", revision=1)

    assert state.safety.state is SafetyState.UNKNOWN
    assert state.safety.evaluated_at_revision is None


def test_updating_a_slot_does_not_drop_the_verdict() -> None:
    """`with_slot` is the path every turn takes; losing the verdict there would
    look exactly like «safety has not run», on a conversation where it had.

    Fail-closed, so nobody would be harmed — and nobody would notice either,
    which is how a whole mechanism stops working while its tests stay green.
    """
    state = ConversationState(conversation_id="c", revision=2).with_safety(_verdict(revision=2))

    updated = state.with_slot("city", SlotValue(state=SlotState.KNOWN, value="Пенза"))

    assert updated.safety == state.safety


def test_a_new_epoch_does_not_inherit_the_previous_verdict(fake_redis: FakeRedis) -> None:
    """Two hours of silence end the epoch; the verdict does not survive it.

    A verdict computed for the revision before the gap, carried into a state
    numbered after it, is precisely the stale input §15.2 blocks on — wearing a
    fresh state's number so that the staleness check cannot see it.
    """
    saved = _state("conv-epoch").with_safety(_verdict(revision=1))
    state_mod.save(saved)
    fake_redis.expire_key(state_mod._state_key("conv-epoch"))

    after = state_mod.load("conv-epoch")
    assert after.lifecycle is StateLifecycle.EXPIRED
    fresh = state_mod.open_epoch("conv-epoch", after=after)

    assert fresh.safety.state is SafetyState.UNKNOWN


# ─── the codec declares its own completeness, with a guard behind it ────────


def test_the_codec_knows_every_field_of_the_verdict() -> None:
    """A field added to `SafetyResult` must fail a check, not be dropped at read.

    This is the guard the field list claims to have. Without it, the next field
    on `SafetyResult` — `handoff`, arriving with §127 — would be encoded
    nowhere and read back as absent, and the only symptom would be a
    `ValueError` from `__post_init__` on live data.

    Stated as a set difference in both directions on purpose: a field the codec
    carries but the type no longer has is a payload key nothing will ever read,
    and that is how a format grows a ghost.
    """
    declared = {f.name for f in dataclasses.fields(SafetyResult)}
    carried = set(state_mod._SAFETY_CODEC_FIELDS)

    assert declared, "у SafetyResult не нашлось полей — измерялся не тот предмет"
    assert declared - carried == set(), (
        f"у SafetyResult есть поля, которым не научили кодек: {sorted(declared - carried)}. "
        "Их нужно назвать в _SAFETY_CODEC_FIELDS, _encode_safety и _decode_safety — "
        "иначе они молча теряются при чтении из Redis."
    )
    assert carried - declared == set(), (
        f"кодек несёт поля, которых у SafetyResult больше нет: {sorted(carried - declared)}"
    )


def test_the_payload_carries_exactly_the_declared_keys() -> None:
    """The guard above compares names; this one compares what is actually written.

    Two lists agreeing with each other is not the same as either agreeing with
    the bytes in Redis.
    """
    state = ConversationState(conversation_id="c", revision=1).with_safety(_verdict(revision=1))
    encoded = json.loads(state_mod._encode(state))

    assert "safety" in encoded
    assert set(encoded["safety"]) == set(state_mod._SAFETY_CODEC_FIELDS)


def test_wall_clock_lives_only_in_activated_at_and_never_in_the_digest() -> None:
    """Стенные часы разрешены ровно в одном поле — и не в ключе идемпотентности.

    ### Чем снята прежняя форма

    Здесь стоял запрет на любое поле с часами в payload вердикта. Его снял
    **свод владельца 2026-09-11 §3**: safety-контекст между ходами обязан нести
    `activated_at`. Слово владельца старше довода исполнителя, и сторож
    переворачивается вслух — с указанием, чем снят, — а не молча удаляется.

    ### Что от прежнего довода остаётся, и он остаётся целиком

    Довод был не «часов не должно быть», а «часов не должно быть **в ключе
    идемпотентности**»: время в дайджесте делает два одинаковых решения
    разными, и идемпотентность исчезает при всех зелёных проверках, потому что
    ключи и обязаны различаться, когда вход различен. Механизм работает ровно
    наоборот и выглядит работающим.

    Поэтому сторож сужается, а не снимается:

        payload   часы разрешены ТОЛЬКО в activated_at
        digest    часов нет вовсе — два вердикта, различающиеся только
                  activated_at, обязаны дать один дайджест
    """
    at = datetime(2026, 9, 11, 14, 30, tzinfo=UTC)
    verdict = SafetyResult(
        state=SafetyState.CLARIFY,
        evaluated_at_revision=1,
        handoff=Handoff.RECOMMENDED,
        activated_at=at,
    )
    encoded = json.loads(
        state_mod._encode(ConversationState(conversation_id="c", revision=1).with_safety(verdict))
    )

    assert "safety" in encoded, "вердикт не записан — проверять в нём нечего"
    clockish = [k for k in encoded["safety"] if "_at" in k and k != "evaluated_at_revision"]
    assert clockish == ["activated_at"], f"часы вне разрешённого поля: {clockish}"
    # Положительный контроль: часы действительно доехали до payload, а не
    # потерялись — иначе «только в activated_at» выполнялось бы и пустотой.
    assert encoded["safety"]["activated_at"] == at.isoformat()

    later = SafetyResult(
        state=SafetyState.CLARIFY,
        evaluated_at_revision=1,
        handoff=Handoff.RECOMMENDED,
        activated_at=at + timedelta(hours=3),
    )
    assert verdict.digest_fields() == later.digest_fields(), (
        "activated_at попал в ключ идемпотентности: два одинаковых решения "
        "с разным временем активации стали разными"
    )
    # И обратный контроль, чтобы «дайджесты равны» не означало «дайджест пуст»:
    # содержательное различие обязано менять дайджест.
    other_rule = SafetyResult(
        state=SafetyState.CLARIFY,
        evaluated_at_revision=1,
        handoff=Handoff.RECOMMENDED,
        rule_id="pre_check.regex:other",
        activated_at=at,
    )
    assert verdict.digest_fields() != other_rule.digest_fields()


# ─── the tolerance carries its own measure ──────────────────────────────────


def test_unreadable_entries_are_counted_not_only_logged(fake_redis: FakeRedis) -> None:
    """«Это была раскатка» обязано быть утверждением с числом.

    Терпимость к нечитаемой форме держится на одном допущении: это раскатка,
    значит минуты, значит кончится. Проверить его можно только частотой — а
    строки в логе, которые никто не считает, делают «только что выкатились» и
    «сломано со вчера» **одинаковыми**: и то и другое выглядит россыпью строк.

    Несколько за час — деплой. Те же строки каждый час — дефект, спрятавшийся
    в шуме деплоев.
    """
    now = datetime(2026, 9, 11, 14, 30, tzinfo=UTC)

    for _ in range(3):
        state_mod._decode(_unreadable_blob())

    tally = state_mod.count_unreadable_safety_entries(now=now)

    assert tally, "счётчик пуст — мерили не то, что считали"
    assert sum(tally.values()) == 3


def test_a_healthy_read_counts_nothing(fake_redis: FakeRedis) -> None:
    """Положительный контроль к счётчику, и оба утверждения сведены в одно число.

    Счётчик, считающий каждое чтение, выглядел бы работающим и не отвечал бы ни
    на один вопрос: «сколько раз сломалось» и «сколько раз прочитали» — разные
    числа, и одинаково ненулевые.

    Поэтому здесь сначала идут три **здоровых** чтения, потом одно нечитаемое, и
    проверяется, что счётчик показывает ровно единицу. Число говорит сразу две
    вещи: счётчик на этой корзине работает, и здоровые чтения в неё не попали.
    Отдельная проверка «пусто» этого не даёт — пустота одинаково совместима с
    «не считает лишнего» и «не считает вовсе».
    """
    now = datetime(2026, 9, 11, 14, 30, tzinfo=UTC)
    saved = _state("conv-healthy").with_safety(_verdict(revision=1))
    state_mod.save(saved)
    state_mod.load("conv-healthy")
    state_mod._decode(json.dumps({"v": 1, "conversation_id": "c", "revision": 1, "slots": {}}))

    state_mod._decode(_unreadable_blob())
    tally = state_mod.count_unreadable_safety_entries(now=now)

    assert tally, "счётчик не сработал даже на нечитаемом входе — мерили не тот предмет"
    assert sum(tally.values()) == 1


def test_counting_never_costs_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis лёг — чтение обязано продолжиться, а не упасть.

    Запись ради наблюдаемости, способная бросить, превращает деградировавшее
    чтение в потерянный ход человека. Это дефект хуже измеряемого, и заведённый
    самим измерением.
    """

    def _boom() -> object:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(state_mod, "_redis_client", _boom)

    decoded = state_mod._decode(_unreadable_blob())

    assert decoded.safety.state is SafetyState.UNKNOWN


# ─── §127 приехало позже кодека ─────────────────────────────────────────────


def test_a_verdict_stored_before_handoff_existed_is_unknown_not_none(
    fake_redis: FakeRedis,
) -> None:
    """Вердикт есть, обещания нет — читаем как «не сказали», а не как «не нужно».

    Такой блоб появляется ровно один раз в жизни системы: между тем, как кодек
    научился писать `"safety"`, и тем, как §127 добавила `handoff`. Прочитать
    его как `Handoff.NONE` значило бы **пообещать человеку меньше, чем обещал
    движок**, — а §127 и существует, чтобы «мы передадим вас человеку» не
    сливалось с «мы отказываем».

    Ронять ход тоже нельзя: у пропуска невиноватое происхождение — payload
    старше поля. Поэтому это нечитаемая форма: `UNKNOWN`, посчитано, и оттого
    заметно. Радиус ограничен TTL состояния: через два часа после выкладки
    таких блобов не существует.
    """
    blob = json.dumps(
        {
            "v": 2,
            "conversation_id": "c",
            "revision": 1,
            "slots": {},
            "safety": {
                "state": "clarify",
                "evaluated_at_revision": 1,
                "rule_id": "pre_check.regex:pregnancy",
                "policy_version": "safety-2026-09-01",
                "required_slots": [],
                "forbidden_capabilities": [],
            },
        }
    )

    decoded = state_mod._decode(blob)

    # Сначала — что блоб вообще прочитан, а не свалился на полпути.
    assert decoded.conversation_id == "c"
    # И предмет: вердикт не подставлен, а объявлен неизвестным.
    assert decoded.safety.state is SafetyState.UNKNOWN
    assert decoded.safety.handoff is None
    assert state_mod.count_unreadable_safety_entries(
        now=datetime(2026, 9, 11, 14, 30, tzinfo=UTC)
    ), "неполный вердикт обязан попасть в счётчик, иначе он невидим"


def test_the_promise_survives_redis(fake_redis: FakeRedis) -> None:
    """Положительный контроль: `handoff` доезжает туда и обратно.

    Без него предыдущий тест зеленел бы на кодеке, который не пишет `handoff`
    никогда — то есть на том самом дефекте, который сторож полноты и поймал.
    """
    saved = _state("conv-promise").with_safety(_verdict(revision=1))
    state_mod.save(saved)

    result = state_mod.load("conv-promise")

    assert result.state is not None
    assert result.state.safety.handoff is Handoff.RECOMMENDED
