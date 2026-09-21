"""DRF-2243 — «забудь всё» и удаление аккаунта обезличивают и обращение к оператору.

Замер (dev a282d3d8): ``handoff.AdminTask`` хранит две копии слов человека,
которых стирание не касалось:

* ``transcript_snapshot`` — ``package_transcript``: последние 20 сообщений с
  полным ``content``, ``display_name``, ``channel_user_id``, ``phone_hash``;
* ``reason`` — на двух путях handoff это «Trigger phrase: {80 символов
  реплики}» (``skills/human_handoff``, ``orchestrator/handoff``), у жалобы после
  визита — фрагмент отзыва (``booking/services/feedback``).

``conversations.erasure.anonymize_dialogue`` обезличивал ``Message`` / черновики
/ ``skill_state`` / Redis и не трогал задачу; удаление аккаунта оставляло её
(``PROTECT``) со снимком целиком. Решение главного окна: пустая строка, не
редакция — редакция оставила бы слова в поле, которое читает оператор. Строка
задачи, тип, статус, время — остаются (форензика).
"""

# Фикстуры ``person`` / ``fake_redis`` импортированы из test_erasure и
# принимаются тестами параметрами — это не переопределение (F811).
# ruff: noqa: F811

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversations.erasure import anonymize_dialogue
from apps.conversations.models import ArchivedMessage
from apps.conversations.tests.test_erasure import (  # noqa: F401 — фикстуры
    _conversation,
    _message,
    fake_redis,
    person,
)
from apps.handoff.models import AdminTask
from apps.handoff.services import package_transcript

pytestmark = pytest.mark.django_db

WORDS = "мне плохо после процедуры, телефон 89001234567"


def _task(conversation, *, reason: str = "", status: str = AdminTask.Status.OPEN) -> AdminTask:
    return AdminTask.all_tenants.create(
        tenant=conversation.tenant,
        bot_user=conversation.bot_user,
        conversation=conversation,
        task_type=AdminTask.TaskType.HANDOFF,
        status=status,
        reason=reason,
        transcript_snapshot=package_transcript(conversation),
        assigned_queue="duty",
    )


def _person_named(person):
    person.display_name = "Мария Иванова"
    person.phone = "+79001234567"
    person.save(update_fields=["display_name", "phone"])
    return person


class TestSnapshotIsAnonymized:
    def test_forget_all_blanks_words_and_identifiers(self, person, fake_redis) -> None:
        person = _person_named(person)
        conv = _conversation(person)
        _message(conv, WORDS)
        task = _task(conv, reason=f"Trigger phrase: {WORDS[:80]}")
        snap = task.transcript_snapshot
        assert snap["messages"][0]["content"] == WORDS  # положительно: слова в снимке были
        assert snap["bot_user"]["display_name"] == "Мария Иванова"

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        task.refresh_from_db()
        snap = task.transcript_snapshot
        assert [m["content"] for m in snap["messages"]] == [""]
        assert snap["bot_user"]["display_name"] == ""
        assert snap["bot_user"]["channel_user_id"] == ""
        assert snap["bot_user"]["phone_hash"] == ""
        assert task.reason == ""

    def test_the_task_row_itself_survives(self, person, fake_redis) -> None:
        conv = _conversation(person)
        _message(conv, WORDS)
        task = _task(conv, status=AdminTask.Status.RESOLVED)

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        task.refresh_from_db()
        assert task.task_type == AdminTask.TaskType.HANDOFF
        assert task.status == AdminTask.Status.RESOLVED
        assert task.transcript_snapshot["messages"][0]["role"] == "user"  # форма снимка на месте

    def test_an_open_task_is_anonymized_too(self, person, fake_redis) -> None:
        """Рекомендация «стирать и открытые» — ждёт слова владельца (вопрос 2);
        PR не сливается до ответа."""
        conv = _conversation(person)
        _message(conv, WORDS)
        task = _task(conv, status=AdminTask.Status.OPEN)

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        task.refresh_from_db()
        assert task.transcript_snapshot["messages"][0]["content"] == ""

    def test_a_reason_code_is_kept(self, person, fake_redis) -> None:
        """Код причины (``booking_handoff``) — не слова человека: остаётся."""
        conv = _conversation(person)
        _message(conv, WORDS)
        task = _task(conv, reason="booking_handoff")

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        task.refresh_from_db()
        assert task.reason == "booking_handoff"


class TestTheCutoff:
    def test_turns_after_the_request_stay_in_the_snapshot(self, person, fake_redis) -> None:
        """Тот же cutoff, что у сообщений: реплики после просьбы — снова свои."""
        conv = _conversation(person)  # до cutoff: иначе диалог вне выборки
        now = timezone.now()
        _message(conv, "до просьбы", at=now - timedelta(minutes=10))
        _message(conv, "после просьбы", at=now + timedelta(minutes=10))
        task = _task(conv)

        anonymize_dialogue([person.id], through=now, reason=ArchivedMessage.Reason.FORGET_ALL)

        task.refresh_from_db()
        contents = [m["content"] for m in task.transcript_snapshot["messages"]]
        assert contents == ["", "после просьбы"]

    def test_another_persons_task_is_untouched(self, person, fake_redis) -> None:
        from apps.identity.models import BotUser

        other = BotUser.all_tenants.create(
            tenant=person.tenant, channel="max", channel_user_id="er-other"
        )
        conv = _conversation(other)
        _message(conv, WORDS)
        task = _task(conv, reason=f"Trigger phrase: {WORDS[:80]}")

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        task.refresh_from_db()
        assert task.transcript_snapshot["messages"][0]["content"] == WORDS
        assert task.reason.startswith("Trigger phrase:")


class TestAccountDelete:
    def test_account_delete_anonymizes_the_snapshot(self, person, fake_redis, settings) -> None:
        from apps.identity.services.privacy import delete_personal_data

        person = _person_named(person)
        conv = _conversation(person)
        _message(conv, WORDS)
        task = _task(conv, reason=f"Trigger phrase: {WORDS[:80]}")
        assert task.transcript_snapshot["messages"][0]["content"] == WORDS  # положительно

        delete_personal_data(person)

        task.refresh_from_db()
        assert task.transcript_snapshot["messages"][0]["content"] == ""
        assert task.transcript_snapshot["bot_user"]["display_name"] == ""
        assert task.reason == ""
        assert AdminTask.all_tenants.filter(pk=task.pk).exists()  # строка на месте
