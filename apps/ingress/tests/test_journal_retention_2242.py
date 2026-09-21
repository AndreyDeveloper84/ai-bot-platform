"""DRF-2242 — сырое тело вебхука в ``WebhookJournal`` живёт не дольше потока.

Замер (dev b1ea3dd9): ``raw_payload`` — полный вебхук MAX (текст, имя) —
хранился бессрочно; «забудь всё» и удаление аккаунта его не знали. Программно
его не читает никто (только показ в админке), поэтому срок согласовывать не с
кем. Решение главного окна:

* тело — тот же ``INGRESS_RAW_RETENTION_HOURS`` (W), что у потоков #1952: одна
  и та же копия, короткий срок одной из двух ничего бы не защищал;
* строка без тела — ``WEBHOOK_JOURNAL_ROW_RETENTION_DAYS`` (90): на ней дедуп
  повторов MAX и служебный след;
* периодическая чистка берёт только «кромку» — то, что недавно перешло срок;
  накопленное раньше — команда ``purge_webhook_journal``, сухой прогон по
  умолчанию, ``--apply`` только по слову владельца.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.ingress.models import WebhookJournal

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ingress_streams_empty")]

W_HOURS = 72
ROW_DAYS = 90


@pytest.fixture(autouse=True)
def _terms(settings):
    settings.INGRESS_RAW_RETENTION_HOURS = W_HOURS
    settings.WEBHOOK_JOURNAL_ROW_RETENTION_DAYS = ROW_DAYS


def _body(user_id: int, text: str = "мне плохо, 89001234567") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Мария"},
            "recipient": {"chat_id": user_id, "chat_type": "dialog"},
            "body": {"mid": f"mid.{user_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _row(user_id: int, *, age: timedelta, event_id: str | None = None) -> WebhookJournal:
    row = WebhookJournal.objects.create(
        channel="max",
        external_event_id=event_id or f"ev-{user_id}-{age.total_seconds():.0f}",
        raw_payload=_body(user_id),
    )
    WebhookJournal.objects.filter(pk=row.pk).update(received_at=timezone.now() - age)
    row.refresh_from_db()
    return row


# ── срок тела и строки: периодическая чистка по кромке ───────────────


class TestSweep:
    def test_a_body_past_w_is_blanked_and_a_fresh_one_kept(self) -> None:
        from apps.ingress.retention import sweep_expired

        old = _row(1, age=timedelta(hours=W_HOURS + 5))
        fresh = _row(2, age=timedelta(hours=5))

        sweep_expired()

        old.refresh_from_db()
        fresh.refresh_from_db()
        assert old.raw_payload == {}
        assert fresh.raw_payload["message"]["body"]["text"]  # положительно: свежее тело на месте

    def test_the_row_survives_blanking_and_still_dedups(self) -> None:
        from apps.ingress.retention import sweep_expired
        from apps.ingress.services import record_webhook

        old = _row(3, age=timedelta(hours=W_HOURS + 5), event_id="ev-dedup")
        sweep_expired()

        assert WebhookJournal.objects.filter(pk=old.pk).exists()
        _row_again, created = record_webhook(
            channel="max", external_event_id="ev-dedup", raw_payload=_body(3)
        )
        assert created is False  # повтор MAX всё ещё распознаётся

    def test_a_row_past_its_term_is_deleted(self) -> None:
        from apps.ingress.retention import sweep_expired

        doomed = _row(4, age=timedelta(days=ROW_DAYS + 2))
        kept = _row(5, age=timedelta(days=ROW_DAYS - 2))

        sweep_expired()

        assert not WebhookJournal.objects.filter(pk=doomed.pk).exists()
        assert WebhookJournal.objects.filter(pk=kept.pk).exists()

    def test_the_backlog_beyond_the_edge_is_the_commands_not_the_sweeps(self) -> None:
        """Накопленное до выкладки — команда по слову владельца, не свип."""
        from apps.ingress.retention import sweep_expired

        backlog_body = _row(6, age=timedelta(days=30))
        backlog_row = _row(7, age=timedelta(days=ROW_DAYS + 60))

        sweep_expired()

        backlog_body.refresh_from_db()
        assert backlog_body.raw_payload != {}
        assert WebhookJournal.objects.filter(pk=backlog_row.pk).exists()


# ── команда для накопленного ─────────────────────────────────────────


class TestBacklogCommand:
    def test_dry_run_is_the_default_and_changes_nothing(self) -> None:
        old = _row(8, age=timedelta(days=30))
        ancient = _row(9, age=timedelta(days=ROW_DAYS + 60))
        out = StringIO()

        call_command("purge_webhook_journal", stdout=out)

        old.refresh_from_db()
        assert old.raw_payload != {}
        assert WebhookJournal.objects.filter(pk=ancient.pk).exists()
        text = out.getvalue()
        assert "dry-run" in text.lower()
        assert "payloads=1" in text and "rows=1" in text

    def test_apply_blanks_and_deletes(self) -> None:
        old = _row(10, age=timedelta(days=30))
        ancient = _row(11, age=timedelta(days=ROW_DAYS + 60))
        fresh = _row(12, age=timedelta(hours=1))

        call_command("purge_webhook_journal", "--apply", stdout=StringIO())

        old.refresh_from_db()
        fresh.refresh_from_db()
        assert old.raw_payload == {}
        assert not WebhookJournal.objects.filter(pk=ancient.pk).exists()
        assert fresh.raw_payload != {}  # положительно: свежее не тронуто


# ── «забудь всё» и удаление аккаунта ─────────────────────────────────


def _person(uid: int):
    import uuid

    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug=f"jr-{uuid.uuid4().hex[:8]}", name="JR")
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=str(uid), ayla_user_id=uuid.uuid4()
    )


class TestErasure:
    def test_forget_all_blanks_the_persons_bodies_only(self, settings) -> None:
        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage

        settings.STRICT_TENANT_SCOPE = "off"
        person = _person(4242)
        mine = _row(4242, age=timedelta(hours=2))
        theirs = _row(5151, age=timedelta(hours=2))

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.raw_payload == {}
        assert theirs.raw_payload["message"]["sender"]["user_id"] == 5151

    def test_a_body_after_the_request_stays(self, settings) -> None:
        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage

        settings.STRICT_TENANT_SCOPE = "off"
        person = _person(4343)
        asked = timezone.now() - timedelta(hours=1)
        before = _row(4343, age=timedelta(hours=2))
        after = _row(4343, age=timedelta(minutes=10), event_id="ev-after")

        anonymize_dialogue([person.id], through=asked, reason=ArchivedMessage.Reason.FORGET_ALL)

        before.refresh_from_db()
        after.refresh_from_db()
        assert before.raw_payload == {}
        assert after.raw_payload != {}

    def test_a_non_max_shell_with_the_same_id_does_not_match(self, settings) -> None:
        """Чужое пространство id: telegram-оболочка с тем же числом — не автор."""
        import uuid

        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage
        from apps.identity.models import BotUser
        from apps.tenancy.models import Tenant

        settings.STRICT_TENANT_SCOPE = "off"
        tenant = Tenant.objects.create(slug=f"jr-{uuid.uuid4().hex[:8]}", name="TG")
        tg = BotUser.all_tenants.create(tenant=tenant, channel="telegram", channel_user_id="4444")
        stranger = _row(4444, age=timedelta(hours=2))

        anonymize_dialogue(
            [tg.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        stranger.refresh_from_db()
        assert stranger.raw_payload != {}

    def test_account_delete_blanks_the_persons_bodies(self, settings) -> None:
        from apps.identity.services.privacy import delete_personal_data

        settings.STRICT_TENANT_SCOPE = "off"
        person = _person(4545)
        mine = _row(4545, age=timedelta(hours=2))
        assert mine.raw_payload != {}  # положительно

        delete_personal_data(person)

        mine.refresh_from_db()
        assert mine.raw_payload == {}
        assert WebhookJournal.objects.filter(pk=mine.pk).exists()  # строка на месте


# ── вариант (в): связь строки с человеком рвётся по его просьбе ──────


class TestLinkIsSevered:
    """Строка без тела — псевдонимизированные ПДн: ``trace_id`` ведёт к
    ``Message`` → ``BotUser.channel_user_id``, а ``external_event_id`` (mid /
    callback_id) держатель токена может запросить у MAX. При «забудь всё» и
    удалении аккаунта у строк человека ``trace_id`` обнуляется, а event id
    заменяется односторонним хешем — дедуп повтора того же события остаётся."""

    def test_trace_and_event_id_no_longer_point_at_the_person(self, settings) -> None:
        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage

        settings.STRICT_TENANT_SCOPE = "off"
        person = _person(4646)
        mine = _row(4646, age=timedelta(hours=2), event_id="mid.4646:1")
        theirs = _row(5252, age=timedelta(hours=2), event_id="mid.5252:1")
        WebhookJournal.objects.filter(pk__in=[mine.pk, theirs.pk]).update(trace_id="trace-x")

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.trace_id == ""
        assert mine.external_event_id != "mid.4646:1"
        assert "4646" not in mine.external_event_id
        assert theirs.trace_id == "trace-x"  # положительно: чужая строка не тронута
        assert theirs.external_event_id == "mid.5252:1"

    def test_a_late_retry_of_an_erased_event_is_still_deduped(self, settings) -> None:
        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage
        from apps.ingress.services import record_webhook

        settings.STRICT_TENANT_SCOPE = "off"
        person = _person(4747)
        _row(4747, age=timedelta(minutes=5), event_id="mid.4747:1")

        anonymize_dialogue(
            [person.id], through=timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
        )

        _again, created = record_webhook(
            channel="max", external_event_id="mid.4747:1", raw_payload=_body(4747)
        )
        assert created is False
        assert WebhookJournal.objects.filter(external_event_id="mid.4747:1").count() == 0

    def test_a_fresh_event_is_recorded_as_before(self) -> None:
        from apps.ingress.services import record_webhook

        _row_new, created = record_webhook(
            channel="max", external_event_id="mid.new:1", raw_payload=_body(4848)
        )
        assert created is True
