"""DRF-1950 — ручная повторная постановка задания удаления в Ayla (решение В3).

Алерт об исчерпании называет оператору команду ``manage.py ayla_erasure_requeue``.
Без аргумента ``--apply`` она только показывает, что сделала бы (сухой прогон по
умолчанию); с ним — возвращает исчерпанное задание в очередь на ближайший проход.
Закрытые задания (completed / superseded_by_account_deletion) команда не трогает:
стирание уже подтверждено или его закрыло удаление аккаунта. Вывод — число
затронутых заданий и номера, без внешних идентификаторов и субъекта Ayla.
На пилоте ``--apply`` выполняет главное окно по слову владельца.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def job(db):
    from apps.identity.models import AylaErasureJob

    tenant = Tenant.objects.create(slug="erasure-requeue", name="Erasure Requeue")
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="939393",
        chat_id="939393",
        ayla_user_id=uuid.uuid4(),
    )
    return AylaErasureJob.objects.create(
        bot_user=user,
        ayla_user_id=user.ayla_user_id,
        external_user_id="bot:max:939393",
        source="revoke_data_storage",
        status=AylaErasureJob.Status.FAILED,
        attempts=10,
        next_attempt_at=None,
        last_error_kind="transport",
        alerted_at=timezone.now() - timedelta(hours=1),
    )


def _no_personal_values(text: str, job) -> None:
    for secret in ("939393", "bot:max", str(job.ayla_user_id)):
        assert secret not in text, secret


def test_the_default_is_a_dry_run_that_changes_nothing(job) -> None:
    from apps.identity.models import AylaErasureJob

    out = StringIO()
    call_command("ayla_erasure_requeue", str(job.pk), stdout=out)

    job.refresh_from_db()
    assert job.status == AylaErasureJob.Status.FAILED
    assert job.attempts == 10
    text = out.getvalue()
    assert "сухой прогон" in text
    assert "затронуто было бы заданий: 1" in text
    assert str(job.pk) in text
    assert "на пилоте --apply выполняет главное окно по слову владельца" in text
    _no_personal_values(text, job)


def test_apply_puts_an_exhausted_job_back_in_the_queue(job) -> None:
    from apps.identity.models import AylaErasureJob

    before = timezone.now()
    out = StringIO()
    call_command("ayla_erasure_requeue", str(job.pk), "--apply", stdout=out)

    job.refresh_from_db()
    assert job.status == AylaErasureJob.Status.PENDING
    assert job.attempts == 0
    assert job.alerted_at is None
    assert job.next_attempt_at is not None and job.next_attempt_at <= timezone.now()
    assert job.next_attempt_at >= before - timedelta(seconds=1)
    text = out.getvalue()
    assert "затронуто заданий: 1" in text
    assert str(job.pk) in text
    _no_personal_values(text, job)


@pytest.mark.parametrize("closed", ["completed", "superseded_by_account_deletion"])
def test_a_closed_job_is_never_requeued(job, closed) -> None:
    from apps.identity.models import AylaErasureJob

    AylaErasureJob.objects.filter(pk=job.pk).update(status=closed)
    # Отказ самой команды, а не «Unknown command»: без match тест был зелёным
    # и при отсутствии команды (вхолостую, пойман на красном до правки).
    with pytest.raises(CommandError, match="закрыто"):
        call_command("ayla_erasure_requeue", str(job.pk), "--apply", stdout=StringIO())
    job.refresh_from_db()
    assert job.status == closed
