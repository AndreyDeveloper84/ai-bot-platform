"""DRF-2746 — переписка человека БЕЗ связки с Ayla дочищается после упавшего шага.

Дефект (замер ayla-9c на ``c38b54b8``): отзыв согласия у несвязанного человека
идёт веткой ``no_state`` каскада удаления, ``forget_all_requested_at`` не
ставится — ставить его некуда, первичный ключ ``UserPersonalContext`` и есть
``ayla_user_id``. Если шаг ``dialogue_anonymize`` упал, строки ``Message``
оставались в базе и повтора не было: ежечасный свип ходит от UPC.

Правка: носитель повтора — отзыв ``personal_data`` (``withdrawn_at`` строки
согласия). Он пишется шагом 3 каскада раньше шага 6 и есть у связанного и у
несвязанного одинаково. Отсечка — момент отзыва, не «сейчас».

* d1 — сквозной: продуктовый отзыв с упавшим обезличиванием, затем свип —
  реплики с фразой 0 (красный до правки: свип человека не видит);
* d2 — признак повтора стоит сразу после упавшего каскада;
* d3 — повторная выдача согласия новые реплики не трёт;
* d4 — отсечка не «сейчас»: реплика после отзыва и разговор, начатый после
  него, целы;
* d5 — отзыв НЕ personal_data (медданные, маркетинг, расчёт) переписку не
  трогает — контроль против лишнего стирания;
* d6 — связанный путь прежний: «забудь всё» дочищается своей выборкой, вторая
  выборка без отзыва его не берёт;
* d7 — перепись: personal_data отзывают только источники стирания.

Данные синтетические.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.conversations.models import ArchivedMessage, Conversation, Message
from apps.conversations.services import (
    record_global_message,
    resolve_active_global_conversation,
)
from apps.identity.models import BotUser, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.forget_all_sweep import sweep_pending_forget_all
from apps.identity.services.memory_deleter import request_forget_all

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
MARKER = "ZETA-MARKER-2746"

# Моменты — литералами: отсечка сравнивается с ними, а не с собой.
T_BEFORE = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
T_WITHDRAW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
T_AFTER = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ingress_streams_empty")]


@pytest.fixture(autouse=True)
def redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    from apps.conversations.tests.test_erasure import _FakeRedis
    from apps.llm import pii_tokenizer
    from apps.orchestrator.decision_readiness import state as dre_state
    from apps.orchestrator.memory import short_term

    fake = _FakeRedis()
    monkeypatch.setattr(dre_state, "_redis_client", lambda: fake)
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    return fake


def _person(uid: str, *, ayla_user_id: uuid.UUID | None = None) -> BotUser:
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, ayla_user_id=ayla_user_id
    )


def _grant(bot_user: BotUser, consent_type: str = PD) -> ConsentRecord:
    return record_global_consent(
        bot_user, consent_type=consent_type, source="test-2746", document_version="v1"
    )


def _withdrawn(bot_user: BotUser, at: datetime, consent_type: str = PD) -> None:
    """Грант, отозванный в момент ``at`` — тем же полем, которое ставит ``withdraw``."""
    record = _grant(bot_user, consent_type)
    ConsentRecord.all_tenants.filter(pk=record.pk).update(withdrawn_at=at)


def _conversation(bot_user: BotUser, opened: datetime) -> Conversation:
    conversation = resolve_active_global_conversation(bot_user)
    assert conversation is not None
    Conversation.all_tenants.filter(pk=conversation.pk).update(created_at=opened)
    conversation.refresh_from_db()
    return conversation


def _say(conversation: Conversation, text: str, at: datetime) -> Message:
    message = record_global_message(conversation, role="user", content=text)
    Message.all_tenants.filter(pk=message.pk).update(created_at=at)
    return message


def _still_said(bot_user: BotUser, text: str = MARKER) -> int:
    return Message.all_tenants.filter(
        conversation__bot_user=bot_user, content__contains=text
    ).count()


def _revoke_with_failed_anonymisation(monkeypatch: pytest.MonkeyPatch, bot_user: BotUser) -> Any:
    """Продуктовый отзыв хранения, при котором обезличивание переписки падает."""
    from apps.consent.customer import revoke_data_storage
    from apps.conversations import erasure

    def down(conversation_id: Any) -> None:
        raise ConnectionError("dialogue redis down")

    with monkeypatch.context() as patched:
        patched.setattr(erasure, "_clear_redis_stores", down)
        return revoke_data_storage(bot_user)


# --------------------------------------------------------------------- #
# d1–d2 — дефект
# --------------------------------------------------------------------- #
def test_d1_unlinked_revoke_with_failed_anonymisation_is_finished_by_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user = _person("2746101")
    _grant(bot_user)
    conversation = _conversation(bot_user, opened=T_BEFORE)
    _say(conversation, f"я {MARKER}", at=T_BEFORE + timedelta(minutes=1))

    result = _revoke_with_failed_anonymisation(monkeypatch, bot_user)

    # Окно сбоя: согласие снято, обезличивание не прошло, связки нет, фраза в базе.
    steps = {step.step: (step.ok, step.detail) for step in result.steps}
    assert steps["consent_withdraw"][0] is True
    assert steps["memory_delete"] == (True, "no_state")
    assert steps["dialogue_anonymize"][0] is False
    assert BotUser.all_tenants.get(pk=bot_user.pk).ayla_user_id is None
    assert not UserPersonalContext.objects.exists()
    assert _still_said(bot_user) == 1

    summary = sweep_pending_forget_all()

    assert _still_said(bot_user) == 0
    assert ArchivedMessage.all_tenants.filter(conversation=conversation).count() == 1
    assert summary["withdrawal_dialogues_anonymized"] == 1
    # Повтор идемпотентен: дочищенное больше не кандидат.
    assert sweep_pending_forget_all()["withdrawal_dialogue_candidates"] == 0


def test_d2_the_failed_cascade_leaves_the_shell_marked_for_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.identity.services.forget_all_sweep import pending_withdrawal_dialogue_shells

    bot_user = _person("2746201")
    _grant(bot_user)
    _say(_conversation(bot_user, opened=T_BEFORE), f"я {MARKER}", at=T_BEFORE)
    assert pending_withdrawal_dialogue_shells() == []  # до отзыва — не кандидат

    _revoke_with_failed_anonymisation(monkeypatch, bot_user)

    pending = dict(pending_withdrawal_dialogue_shells())
    assert bot_user.pk in pending
    withdrawn_at = ConsentRecord.all_tenants.get(
        bot_user=bot_user, consent_type=PD, withdrawn_at__isnull=False
    ).withdrawn_at
    assert pending[bot_user.pk] == withdrawn_at


# --------------------------------------------------------------------- #
# d3–d5 — чего свип НЕ трогает
# --------------------------------------------------------------------- #
def test_d3_a_new_grant_after_the_withdrawal_keeps_the_new_replies() -> None:
    bot_user = _person("2746301")
    conversation = _conversation(bot_user, opened=T_BEFORE)
    _say(conversation, f"до отзыва {MARKER}", at=T_BEFORE + timedelta(minutes=1))
    _withdrawn(bot_user, T_WITHDRAW)
    _grant(bot_user)  # человек согласился заново
    _say(conversation, "после новой выдачи ОМЕГА", at=T_AFTER)

    sweep_pending_forget_all()

    assert _still_said(bot_user) == 0
    assert _still_said(bot_user, "ОМЕГА") == 1


def test_d4_the_cutoff_is_the_withdrawal_not_now() -> None:
    bot_user = _person("2746401")
    old = _conversation(bot_user, opened=T_BEFORE)
    _say(old, f"до отзыва {MARKER}", at=T_BEFORE + timedelta(minutes=1))
    _say(old, "после отзыва СИГМА", at=T_WITHDRAW + timedelta(minutes=1))
    _withdrawn(bot_user, T_WITHDRAW)

    sweep_pending_forget_all()

    assert _still_said(bot_user) == 0
    assert _still_said(bot_user, "СИГМА") == 1
    old.refresh_from_db()
    assert old.anonymized_through == T_WITHDRAW


@pytest.mark.parametrize(
    "consent_type",
    [
        ConsentRecord.ConsentType.HEALTH.value,
        ConsentRecord.ConsentType.MARKETING.value,
        ConsentRecord.ConsentType.MEMORY_GREEN.value,
    ],
)
def test_d5_a_withdrawal_that_is_not_personal_data_does_not_erase_the_dialogue(
    consent_type: str,
) -> None:
    from apps.identity.services.forget_all_sweep import pending_withdrawal_dialogue_shells

    bot_user = _person(f"2746-5-{consent_type}")
    _say(_conversation(bot_user, opened=T_BEFORE), f"я {MARKER}", at=T_BEFORE)
    _withdrawn(bot_user, T_WITHDRAW, consent_type)
    # Положительный контроль на соседе: тот же сценарий с personal_data —
    # кандидат. Без него ноль ниже доказывал бы только пустую выборку.
    twin = _person(f"2746-5-pd-{consent_type}")
    _say(_conversation(twin, opened=T_BEFORE), f"я {MARKER}", at=T_BEFORE)
    _withdrawn(twin, T_WITHDRAW)
    assert dict(pending_withdrawal_dialogue_shells()).keys() == {twin.pk}

    sweep_pending_forget_all()

    assert _still_said(twin) == 0
    assert _still_said(bot_user) == 1


# --------------------------------------------------------------------- #
# d6 — связанный путь
# --------------------------------------------------------------------- #
def test_d6_the_linked_forget_all_path_is_unchanged() -> None:
    user_id = uuid.uuid4()
    UserPersonalContext.objects.create(user_id=user_id)
    request_forget_all(user_id)
    requested_at = UserPersonalContext.objects.get(user_id=user_id).forget_all_requested_at
    assert requested_at is not None
    bot_user = _person("2746601", ayla_user_id=user_id)
    conversation = _conversation(bot_user, opened=requested_at - timedelta(minutes=10))
    _say(conversation, f"я {MARKER}", at=requested_at - timedelta(minutes=5))

    summary = sweep_pending_forget_all()

    # Своей выборкой, со своей отсечкой; вторая — без отзыва — его не брала.
    assert summary["users_swept"] == 1
    assert summary["withdrawal_dialogue_candidates"] == 0
    assert _still_said(bot_user) == 0
    conversation.refresh_from_db()
    assert conversation.anonymized_through == requested_at


# --------------------------------------------------------------------- #
# d7 — перепись отзывов personal_data
# --------------------------------------------------------------------- #
APPS = Path(__file__).resolve().parents[3]

#: Функции, которые отзывают personal_data (вместе с каскадом §8.4).
_PD_WITHDRAWERS = {"withdraw_personal_data_for_bot_users", "withdraw_personal_data"}

#: Константы-источники, которые перепись разрешает по имени.
_KNOWN_SOURCE_NAMES = {"DATA_STORAGE_WITHDRAW_SOURCE": "miniapp:profile_data_storage_revoke"}


def _source_of(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "source":
            if isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
            if isinstance(kw.value, ast.Name):
                return _KNOWN_SOURCE_NAMES.get(kw.value.id, f"<name {kw.value.id}>")
            return "<expr>"
    return None


def test_d7_only_erasing_sources_withdraw_personal_data() -> None:
    """Носитель отсечки — любой отзыв personal_data, а источник в строке не хранится.

    Поэтому список источников держит эта перепись. Отзыв personal_data, который
    НЕ стирание, сделал бы свип стирающим переписку по отзыву, который этого не
    обещал, — такой вызов обязан появиться здесь красным и потребовать решения.
    """
    from apps.identity.services.forget_all_sweep import (
        ERASING_PERSONAL_DATA_WITHDRAWAL_SOURCES,
    )

    found: list[tuple[str, int, str | None]] = []
    direct_pd: list[tuple[str, int]] = []
    for path in APPS.rglob("*.py"):
        rel = path.relative_to(APPS.parent).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _PD_WITHDRAWERS:
                source = _source_of(node)
                # Обёртка, передающая свой параметр дальше, — не место отзыва:
                # её место отзыва — у её вызывающих.
                if source != "<name source>":
                    found.append((rel, node.lineno, source))
            elif name in {"withdraw", "withdraw_person_consent"}:
                for kw in node.keywords:
                    if kw.arg == "consent_type" and "PERSONAL_DATA" in ast.dump(kw.value):
                        direct_pd.append((rel, node.lineno))

    # Положительный контроль: перепись видит оба известных места.
    assert {rel for rel, _, _ in found} == {
        "apps/identity/services/privacy.py",
        "apps/consent/customer.py",
    }, found
    assert {source for _, _, source in found} <= set(ERASING_PERSONAL_DATA_WITHDRAWAL_SOURCES), (
        found
    )
    assert direct_pd == []
