"""DRF-1950 (M3) — durable-удаление в Ayla: задание, повтор, readback, алерт.

Решение владельца M3: запрос → durable job → идемпотентность → retry/backoff →
authoritative readback → completed; до readback человеку не пишется «Удалено»;
после исчерпания повторов — операционный алерт.

Readback — каталожная ручка C5.3 ``…/personal-data/erasure-status/``
(beautygo_backend, DRF-1984): состояние строки личного профиля по каждой
личности субъекта и вердикт. Бот считает стирание подтверждённым, только если
вердикт ``erased`` и КАЖДАЯ личность ``erased`` — пустая лениво созданная
строка (``not_erased``) стиранием не считается.

Всё — за флагом ``AYLA_ERASURE_RETRY_ENABLED`` (выключен до выкладки каталога).
При выключенном флаге сегодняшнее поведение — «…удалены.» без readback: это
названный долг против правила владельца, а не «как было — значит верно»
(``test_flag_off_keeps_todays_behaviour_as_a_named_debt``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.identity.models import BotUser
from apps.identity.services.privacy import delete_personal_data
from apps.integrations.ayla.personal_context_client import (
    PersonalContextAuthError,
    PersonalContextTransportError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SOURCE = "revoke_data_storage"


def _identity(context_row: str, erased: bool, kind: str = "account") -> dict:
    return {"kind": kind, "context_row": context_row, "erased": erased}


CONFIRMED = {"erased": True, "identities": [_identity("tombstone", True)]}
NOT_CONFIRMED = {"erased": False, "identities": [_identity("holds_values", False)]}
NOT_ERASED_ROW = {"erased": False, "identities": [_identity("not_erased", False)]}
#: Противоречивый ответ: общий вердикт «стёрто», а личность — нет. Бот не верит
#: одному флагу (fail-closed).
INCONSISTENT = {
    "erased": True,
    "identities": [_identity("tombstone", True), _identity("not_erased", False, "linked_identity")],
}


class _Ayla:
    """Каталог как его видит бот: DELETE и readback стирания."""

    def __init__(
        self, *, delete_exc: Exception | None = None, statuses: list | None = None
    ) -> None:
        self.delete_exc = delete_exc
        self.statuses = list(statuses if statuses is not None else [CONFIRMED])
        self.calls: list[tuple[str, str, str]] = []
        self.closed = False

    def delete_personal_data(self, *, ayla_user_id: str, external_user_id: str) -> None:
        self.calls.append(("delete", ayla_user_id, external_user_id))
        if self.delete_exc is not None:
            raise self.delete_exc

    def get_erasure_status(self, *, ayla_user_id: str, external_user_id: str) -> dict:
        self.calls.append(("status", ayla_user_id, external_user_id))
        item = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True

    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="erasure-retry", name="Erasure Retry")


@pytest.fixture
def ayla_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bot_user(tenant, ayla_user_id) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="424242",
        chat_id="424242",
        ayla_user_id=ayla_user_id,
    )


@pytest.fixture
def enabled(settings):
    settings.AYLA_ERASURE_RETRY_ENABLED = True
    return settings


def _ayla_step(result):
    return next(s for s in result.steps if s.step == "ayla_delete")


# ── Каскад: первая попытка синхронно, readback решает ────────────────────────


class TestFirstAttemptInTheCascade:
    def test_a_confirmed_readback_completes_the_job_and_the_step_is_ok(
        self, enabled, bot_user, ayla_user_id
    ):
        from apps.identity.models import AylaErasureJob

        ayla = _Ayla(statuses=[CONFIRMED])
        result = delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        step = _ayla_step(result)
        assert (step.ok, step.detail) == (True, "confirmed")
        assert ayla.verbs() == ["delete", "status"]
        job = AylaErasureJob.objects.get()
        assert job.status == AylaErasureJob.Status.COMPLETED
        assert job.ayla_user_id == ayla_user_id
        assert job.attempts == 1
        assert job.completed_at is not None
        assert job.source == SOURCE

    def test_an_unconfirmed_readback_leaves_the_job_pending_and_says_started(
        self, enabled, bot_user
    ):
        from apps.identity.models import AylaErasureJob

        before = timezone.now()
        result = delete_personal_data(
            bot_user, client=_Ayla(statuses=[NOT_CONFIRMED]), retry_source=SOURCE
        )  # type: ignore[arg-type]

        step = _ayla_step(result)
        assert (step.ok, step.detail) == (False, "deletion_started")
        assert result.deletion_started is True
        job = AylaErasureJob.objects.get()
        assert job.status == AylaErasureJob.Status.PENDING
        assert job.attempts == 1
        assert job.last_error_kind == "not_confirmed"
        assert (
            before + timedelta(seconds=55)
            <= job.next_attempt_at
            <= timezone.now() + timedelta(seconds=65)
        )

    def test_an_empty_row_never_marked_erased_does_not_complete_the_job(self, enabled, bot_user):
        """Условие главного окна 15.09: ``not_erased`` → задание НЕ completed."""
        from apps.identity.models import AylaErasureJob

        result = delete_personal_data(
            bot_user, client=_Ayla(statuses=[NOT_ERASED_ROW]), retry_source=SOURCE
        )  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "deletion_started"
        assert AylaErasureJob.objects.get().status == AylaErasureJob.Status.PENDING

    def test_a_verdict_contradicted_by_an_identity_is_not_trusted(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob

        result = delete_personal_data(
            bot_user, client=_Ayla(statuses=[INCONSISTENT]), retry_source=SOURCE
        )  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "deletion_started"
        assert AylaErasureJob.objects.get().status == AylaErasureJob.Status.PENDING

    def test_a_transport_error_schedules_a_retry(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob

        ayla = _Ayla(delete_exc=PersonalContextTransportError("http_500"))
        result = delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "deletion_started"
        assert ayla.verbs() == ["delete"]
        job = AylaErasureJob.objects.get()
        assert (job.status, job.attempts, job.last_error_kind) == (
            AylaErasureJob.Status.PENDING,
            1,
            "transport",
        )

    def test_a_second_request_for_the_same_person_reuses_the_open_job(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob

        for _ in range(2):
            delete_personal_data(
                bot_user, client=_Ayla(statuses=[NOT_CONFIRMED]), retry_source=SOURCE
            )  # type: ignore[arg-type]

        jobs = AylaErasureJob.objects.all()
        assert jobs.count() == 1
        # Решение главного окна S1: новый законный запрос даёт свежие попытки —
        # после второго запроса на счётчике одна попытка, а не две.
        assert jobs.get().attempts == 1

    def test_a_403_after_account_deletion_is_not_a_completion(self, enabled, bot_user):
        """После D3 каталог отвечает боту 403 на всей поверхности — это не «стёрто»."""
        from apps.identity.models import AylaErasureJob

        ayla = _Ayla(statuses=[PersonalContextAuthError("403")])
        result = delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "deletion_started"
        job = AylaErasureJob.objects.get()
        assert (job.status, job.last_error_kind) == (AylaErasureJob.Status.PENDING, "auth")

    def test_a_403_during_account_deletion_closes_the_job_as_superseded(
        self, enabled, bot_user, ayla_user_id
    ):
        """Вариант (б), решение главного окна 15.09: 403 при прочном факте удаления
        аккаунта — задание закрыто без алерта, с аудитом; человеку «удалено» не говорится."""
        from apps.audit.models import AuditLog
        from apps.identity.models import AylaErasureJob, UserPersonalContext

        UserPersonalContext.objects.create(
            user_id=ayla_user_id,
            deletion_requested_at=timezone.now(),
            deletion_request_id=uuid.uuid4(),
        )
        ayla = _Ayla(delete_exc=PersonalContextAuthError("403"))
        with patch("apps.identity.services.ayla_erasure.page", return_value=True) as page:
            result = delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "superseded_by_account_deletion"
        job = AylaErasureJob.objects.get()
        assert job.status == AylaErasureJob.Status.SUPERSEDED
        assert job.external_user_id == ""
        assert page.call_count == 0
        row = AuditLog.all_tenants.get(action="identity.ayla_erasure.superseded")
        assert str(row.target_id) == str(job.pk)
        assert row.payload["reason"] == "account_deletion"
        for secret in ("424242", "bot:max", str(ayla_user_id)):
            assert secret not in str(row.payload), secret

    def test_the_fact_outlives_the_cleared_flag(self, enabled, bot_user, ayla_user_id):
        """Гонка: бот-половина D3 уже сняла флаг, но её след в журнале остался."""
        from apps.audit.services import write_audit
        from apps.identity.models import AylaErasureJob

        write_audit(
            "privacy.account_deletion_bot_half",
            target="ayla_user",
            target_id=ayla_user_id,
            payload={"request_id": "d3-req", "all_ok": True},
        )
        ayla = _Ayla(delete_exc=PersonalContextAuthError("403"))
        with patch("apps.identity.services.ayla_erasure.page", return_value=True) as page:
            delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert AylaErasureJob.objects.get().status == AylaErasureJob.Status.SUPERSEDED
        assert page.call_count == 0

    def test_a_completed_job_keeps_no_external_identifier(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob

        delete_personal_data(bot_user, client=_Ayla(statuses=[CONFIRMED]), retry_source=SOURCE)  # type: ignore[arg-type]

        job = AylaErasureJob.objects.get()
        assert job.status == AylaErasureJob.Status.COMPLETED
        assert job.external_user_id == ""


class TestWhereNoJobIsQueued:
    """Сторожа: зелёные и до правки, и после — их сила в пробах."""

    def test_flag_off_keeps_todays_behaviour_as_a_named_debt(self, settings, bot_user):
        """Известный долг против правила владельца: без флага «удалено» без readback.
        Гасится включением флага после выкладки каталога DRF-1984."""
        settings.AYLA_ERASURE_RETRY_ENABLED = False
        ayla = _Ayla()
        result = delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert (_ayla_step(result).ok, _ayla_step(result).detail) == (True, "")
        assert ayla.verbs() == ["delete"]
        from apps.identity.models import AylaErasureJob

        assert AylaErasureJob.objects.count() == 0

    def test_not_linked_starts_nothing(self, settings, tenant):
        settings.AYLA_ERASURE_RETRY_ENABLED = True
        unlinked = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="515151",
            chat_id="515151",
            ayla_user_id=None,
        )
        ayla = _Ayla()
        with patch(
            "apps.integrations.ayla.identity_client.resolve_identity",
            side_effect=RuntimeError("ayla недоступна в тестах"),
        ):
            result = delete_personal_data(unlinked, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert _ayla_step(result).detail == "not_linked"
        assert ayla.calls == []

    def test_account_deletion_bot_half_never_queues_a_job(self, settings, bot_user, ayla_user_id):
        """В2: D3 не ставится в задание — каталог уже повторяет и сам перечитывает остаток."""
        from apps.identity.services.account_deletion import execute_bot_half

        settings.AYLA_ERASURE_RETRY_ENABLED = True
        ayla = _Ayla(statuses=[NOT_CONFIRMED])
        with patch("apps.identity.services.privacy.PersonalContextHttpClient", return_value=ayla):
            execute_bot_half(ayla_user_id=ayla_user_id, external_user_ids=[], request_id="d3-test")

        assert ayla.verbs() == ["delete"]
        from apps.identity.models import AylaErasureJob

        assert AylaErasureJob.objects.count() == 0


# ── Подметальщик: повтор по расписанию, исчерпание, алерт ───────────────────


def _job(bot_user, **overrides):
    from apps.identity.models import AylaErasureJob

    fields = {
        "bot_user": bot_user,
        "ayla_user_id": bot_user.ayla_user_id,
        "external_user_id": "bot:max:424242",
        "source": SOURCE,
        "status": AylaErasureJob.Status.PENDING,
        "attempts": 1,
        "next_attempt_at": timezone.now() - timedelta(seconds=1),
    }
    fields.update(overrides)
    return AylaErasureJob.objects.create(**fields)


class TestSweep:
    def test_a_due_job_is_retried_and_completed(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        job = _job(bot_user)
        ayla = _Ayla(statuses=[CONFIRMED])
        summary = sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        job.refresh_from_db()
        assert job.status == AylaErasureJob.Status.COMPLETED
        assert job.attempts == 2
        assert ayla.verbs() == ["delete", "status"]
        assert summary["completed"] == 1

    def test_the_sweep_leaves_jobs_that_are_not_due_or_already_closed(
        self, enabled, bot_user, tenant
    ):
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        future = _job(bot_user, next_attempt_at=timezone.now() + timedelta(hours=1))
        other = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="616161",
            chat_id="616161",
            ayla_user_id=uuid.uuid4(),
        )
        done = _job(other, status=AylaErasureJob.Status.COMPLETED)
        ayla = _Ayla()
        sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        assert ayla.calls == []
        future.refresh_from_db()
        done.refresh_from_db()
        assert (future.attempts, done.attempts) == (1, 1)

    def test_each_failure_moves_along_the_schedule(self, enabled, bot_user):
        from apps.identity.services.ayla_erasure import RETRY_DELAYS, sweep_due_jobs

        assert RETRY_DELAYS[:2] == (timedelta(minutes=1), timedelta(minutes=5))
        job = _job(bot_user, attempts=1)
        before = timezone.now()
        sweep_due_jobs(client=_Ayla(statuses=[NOT_CONFIRMED]))  # type: ignore[arg-type]

        job.refresh_from_db()
        assert job.attempts == 2
        assert before + timedelta(minutes=5) - timedelta(seconds=5) <= job.next_attempt_at
        assert job.next_attempt_at <= timezone.now() + timedelta(minutes=5, seconds=5)

    def test_exhaustion_fails_the_job_and_pages_once_without_personal_values(
        self, enabled, bot_user
    ):
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import MAX_ATTEMPTS, sweep_due_jobs

        assert MAX_ATTEMPTS == 10
        job = _job(bot_user, attempts=MAX_ATTEMPTS - 1)
        with patch("apps.identity.services.ayla_erasure.page", return_value=True) as page:
            sweep_due_jobs(client=_Ayla(delete_exc=PersonalContextTransportError("http_500")))  # type: ignore[arg-type]
            job.refresh_from_db()
            assert job.status == AylaErasureJob.Status.FAILED
            assert job.attempts == MAX_ATTEMPTS
            assert job.alerted_at is not None
            assert page.call_count == 1
            severity, title, body = page.call_args.args[:3]
            assert severity == "error"
            assert str(job.pk) in body
            for secret in ("424242", "bot:max", str(bot_user.ayla_user_id)):
                assert secret not in title + body, secret
            # Повторный проход: задание закрыто, второго алерта нет.
            sweep_due_jobs(client=_Ayla())  # type: ignore[arg-type]
            assert page.call_count == 1

    def test_a_403_without_the_fact_is_alerted_when_retries_run_out(self, enabled, bot_user):
        """403 без прочного факта удаления аккаунта — обычная ошибка: исчерпание → алерт."""
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import MAX_ATTEMPTS, sweep_due_jobs

        job = _job(bot_user, attempts=MAX_ATTEMPTS - 1)
        with patch("apps.identity.services.ayla_erasure.page", return_value=True) as page:
            sweep_due_jobs(client=_Ayla(statuses=[PersonalContextAuthError("403")]))  # type: ignore[arg-type]

        job.refresh_from_db()
        assert job.status == AylaErasureJob.Status.FAILED
        assert page.call_count == 1


class TestSchedule:
    def test_beat_runs_the_sweep_every_five_minutes(self, settings):
        from celery.schedules import crontab  # type: ignore[import-untyped]

        entry = settings.CELERY_BEAT_SCHEDULE["identity_ayla_erasure_sweep"]
        assert entry["task"] == "apps.identity.tasks.ayla_erasure_sweep"
        assert isinstance(entry["schedule"], crontab)
        assert {0, 5, 55} <= set(entry["schedule"].minute)

    def test_the_task_is_inert_while_the_flag_is_off(self, settings, bot_user):
        from apps.identity.tasks import ayla_erasure_sweep

        settings.AYLA_ERASURE_RETRY_ENABLED = False
        job = _job(bot_user)
        with patch("apps.identity.services.ayla_erasure.sweep_due_jobs") as sweep:
            assert ayla_erasure_sweep() == {"mode": "disabled"}
        assert sweep.call_count == 0
        job.refresh_from_db()
        assert job.attempts == 1


# ── Ревью, второй заход ──────────────────────────────────────────────────────


class TestReviewPass2:
    def test_an_unexpected_error_in_the_job_does_not_stop_the_cascade(self, enabled, bot_user):
        """B1: сбой механики задания не отменяет локальные шаги и не говорит «запущено»."""
        with patch(
            "apps.identity.services.ayla_erasure.erase_with_readback",
            side_effect=RuntimeError("db hiccup"),
        ):
            result = delete_personal_data(bot_user, client=_Ayla(), retry_source=SOURCE)  # type: ignore[arg-type]

        step = _ayla_step(result)
        assert (step.ok, step.detail) == (False, "")
        assert result.deletion_started is False
        assert "memory_delete" in [s.step for s in result.steps]

    def test_a_new_request_gives_a_reused_job_fresh_attempts(self, enabled, bot_user):
        """S1: новый законный запрос человека — новые попытки, а не последняя из старых."""
        from apps.identity.models import AylaErasureJob

        job = _job(bot_user, attempts=9, next_attempt_at=timezone.now() + timedelta(hours=1))
        result = delete_personal_data(
            bot_user, client=_Ayla(statuses=[NOT_CONFIRMED]), retry_source=SOURCE
        )  # type: ignore[arg-type]

        job.refresh_from_db()
        assert (job.status, job.attempts) == (AylaErasureJob.Status.PENDING, 1)
        assert _ayla_step(result).detail == "deletion_started"

    def test_a_failed_job_is_never_reported_as_started(self, enabled, bot_user):
        """S1: исчерпанное задание — не «запущено»: механизма повтора у него больше нет."""
        from apps.identity.services.ayla_erasure import FAILED, ErasureOutcome

        with patch(
            "apps.identity.services.ayla_erasure.erase_with_readback",
            return_value=ErasureOutcome(state=FAILED, job_id=uuid.uuid4()),
        ):
            result = delete_personal_data(bot_user, client=_Ayla(), retry_source=SOURCE)  # type: ignore[arg-type]

        assert (_ayla_step(result).ok, _ayla_step(result).detail) == (False, "")
        assert result.deletion_started is False

    def test_a_job_closed_meanwhile_is_not_reopened_by_the_first_attempt(
        self, enabled, bot_user, ayla_user_id
    ):
        """S2: пока синхронная попытка ждала каталог, задание закрыли — её итог его не перезапишет."""
        from apps.identity.models import AylaErasureJob

        class _ClosedMeanwhile(_Ayla):
            def get_erasure_status(self, *, ayla_user_id: str, external_user_id: str) -> dict:
                AylaErasureJob.objects.filter(ayla_user_id=ayla_user_id).update(
                    status=AylaErasureJob.Status.COMPLETED, external_user_id=""
                )
                return super().get_erasure_status(
                    ayla_user_id=ayla_user_id, external_user_id=external_user_id
                )

        with patch("apps.identity.services.ayla_erasure.page", return_value=True) as page:
            delete_personal_data(
                bot_user, client=_ClosedMeanwhile(statuses=[NOT_CONFIRMED]), retry_source=SOURCE
            )  # type: ignore[arg-type]

        job = AylaErasureJob.objects.get()
        assert job.status == AylaErasureJob.Status.COMPLETED
        assert job.external_user_id == ""
        assert page.call_count == 0

    def test_the_sweep_claims_the_job_before_calling_ayla(self, enabled, bot_user):
        """S3: задание взято арендой до сети — второй проход подметальщика его не возьмёт."""
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        job = _job(bot_user)
        seen: list = []

        class _Recording(_Ayla):
            def delete_personal_data(self, *, ayla_user_id: str, external_user_id: str) -> None:
                seen.append(AylaErasureJob.objects.get(pk=job.pk).next_attempt_at)
                super().delete_personal_data(
                    ayla_user_id=ayla_user_id, external_user_id=external_user_id
                )

        sweep_due_jobs(client=_Recording(statuses=[NOT_CONFIRMED]))  # type: ignore[arg-type]

        assert seen and seen[0] > timezone.now()

    def test_alerted_at_stays_empty_when_the_page_did_not_go_out(self, enabled, bot_user):
        """S4: «алерт отправлен» — только если он действительно ушёл."""
        from apps.identity.models import AylaErasureJob
        from apps.identity.services.ayla_erasure import MAX_ATTEMPTS, sweep_due_jobs

        job = _job(bot_user, attempts=MAX_ATTEMPTS - 1)
        with patch("apps.identity.services.ayla_erasure.page", return_value=False):
            sweep_due_jobs(client=_Ayla(delete_exc=PersonalContextTransportError("http_500")))  # type: ignore[arg-type]

        job.refresh_from_db()
        assert job.status == AylaErasureJob.Status.FAILED
        assert job.alerted_at is None

    def test_the_first_attempt_in_the_cascade_uses_a_short_client(self, enabled, bot_user):
        """S7: синхронный путь не ждёт всех повторов клиента — остальное повторит задание."""
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(
            "apps.identity.services.privacy.PersonalContextHttpClient", return_value=ayla
        ) as ctor:
            delete_personal_data(bot_user, retry_source=SOURCE)

        assert ctor.call_args.kwargs == {"retries": 1, "timeout": 10}

    def test_a_config_error_is_named_config(self, enabled, bot_user):
        """N1: пробел конфигурации — не «транспорт»: повтор его не лечит."""
        from apps.identity.models import AylaErasureJob
        from apps.integrations.ayla.personal_context_client import PersonalContextConfigError

        delete_personal_data(
            bot_user,
            client=_Ayla(delete_exc=PersonalContextConfigError("no token")),
            retry_source=SOURCE,
        )  # type: ignore[arg-type]

        assert AylaErasureJob.objects.get().last_error_kind == "config"


class TestReviewPass2Guards:
    """Сторожа на подмены, которые ревью назвало непойманными (зелёные и до, и после)."""

    def test_the_pending_job_keeps_the_external_id_snapshot(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob
        from apps.integrations.ayla.user_proxy import external_user_id_for

        expected = external_user_id_for(bot_user)
        assert expected
        delete_personal_data(bot_user, client=_Ayla(statuses=[NOT_CONFIRMED]), retry_source=SOURCE)  # type: ignore[arg-type]

        assert AylaErasureJob.objects.get().external_user_id == expected

    @pytest.mark.parametrize(
        "status",
        [
            {"erased": True, "identities": []},
            {"erased": True, "identities": [_identity("holds_values", True)]},
            {"erased": False, "identities": [_identity("tombstone", True)]},
        ],
        ids=["no-identities", "erased-flag-on-values", "overall-false"],
    )
    def test_a_readback_that_does_not_fully_confirm_keeps_the_job_pending(
        self, enabled, bot_user, status
    ):
        from apps.identity.models import AylaErasureJob

        delete_personal_data(bot_user, client=_Ayla(statuses=[status]), retry_source=SOURCE)  # type: ignore[arg-type]

        assert AylaErasureJob.objects.get().status == AylaErasureJob.Status.PENDING

    def test_a_404_on_delete_still_needs_the_readback(self, enabled, bot_user):
        from apps.identity.models import AylaErasureJob
        from apps.integrations.ayla.personal_context_client import PersonalContextNotFoundError

        ayla = _Ayla(delete_exc=PersonalContextNotFoundError("gone"), statuses=[NOT_CONFIRMED])
        delete_personal_data(bot_user, client=ayla, retry_source=SOURCE)  # type: ignore[arg-type]

        assert ayla.verbs() == ["delete", "status"]
        assert AylaErasureJob.objects.get().status == AylaErasureJob.Status.PENDING
