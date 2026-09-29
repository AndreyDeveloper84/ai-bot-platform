"""Beat-обёртка над `aggregate_ai_metrics_daily` (DRF-1616, блокер B-7).

Задача написана в мае, но в `CELERY_BEAT_SCHEDULE` не стояла ни разу: сводки
не считались, панель показывала «нет данных», и это читалось как «нет
трафика». Здесь стережётся, что запись в расписании появилась, что она
указывает на **зарегистрированную** задачу (а не на опечатку в строке), и что
у обёртки три исхода, различимые по `mode`, — по образцу nutrition_proactive:

    disabled   рубильник закрыт           → базу не трогает вовсе
    dry_run    открыт, DRY_RUN=True       → считает, ничего не пишет
    live       открыт, DRY_RUN=False      → настоящая сводка за вчера

Отдельно — умолчания. Флаги закрыты по умолчанию, поэтому слияние этого PR
ничего не включает: включает владелец, через окружение.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from celery.schedules import crontab  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone as dj_tz

from apps.observability import tasks as tasks_mod
from apps.observability.models import AIDailyMetricSummary, AIRequestMetric
from apps.observability.tasks import aggregate_ai_metrics_daily_beat
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SCHEDULE_KEY = "aggregate_ai_metrics_daily"
TASK_NAME = "apps.observability.tasks.aggregate_ai_metrics_daily_beat"


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="beat-aim", name="Beat AI metrics")


def _metric_yesterday(tenant: Tenant) -> AIRequestMetric:
    """Одна строка метрик со вчерашним `created_at` — auto_now_add перебивается update()."""
    m = AIRequestMetric.all_tenants.create(
        tenant=tenant,
        request_id=uuid.uuid4(),
        message_text_length=1,
        latency_total_ms=100,
        outcome="success",
        fallback_triggered=False,
        intent_classified="book",
        llm_provider="openai",
        llm_tokens_input=1,
        llm_tokens_output=1,
    )
    yesterday_noon = (dj_tz.now() - timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    AIRequestMetric.all_tenants.filter(pk=m.pk).update(created_at=yesterday_noon)
    return m


# ─── расписание ──────────────────────────────────────────────────────────────


def test_schedule_entry_points_at_the_beat_wrapper_at_0305_utc() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE[SCHEDULE_KEY]

    assert entry["task"] == TASK_NAME == aggregate_ai_metrics_daily_beat.name
    sched = entry["schedule"]
    assert isinstance(sched, crontab)
    assert 3 in sched.hour and 5 in sched.minute


def test_schedule_task_name_is_a_registered_celery_task() -> None:
    """Строка в расписании обязана резолвиться в реестре приложения.

    `.name` у импортированного объекта — необходимое, но не достаточное:
    опечатка в строке расписания при верном `.name` дала бы beat, который
    каждую ночь шлёт задачу в никуда, и в логе воркера — `NotRegistered`.
    """
    from config.celery import app

    app.loader.import_default_modules()
    assert settings.CELERY_BEAT_SCHEDULE[SCHEDULE_KEY]["task"] in app.tasks


def test_switches_are_closed_by_default() -> None:
    """Слияние ничего не включает. Открывает владелец через окружение."""
    assert settings.AI_METRICS_BEAT_ENABLED is False
    assert settings.AI_METRICS_BEAT_DRY_RUN is True


# ─── три исхода ──────────────────────────────────────────────────────────────


def test_disabled_does_not_touch_the_database(settings, django_assert_num_queries) -> None:
    settings.AI_METRICS_BEAT_ENABLED = False

    with django_assert_num_queries(0):
        result = aggregate_ai_metrics_daily_beat()

    assert result == {"mode": "disabled"}


def test_dry_run_counts_but_writes_nothing_and_pages_nobody(settings, tenant) -> None:
    settings.AI_METRICS_BEAT_ENABLED = True
    settings.AI_METRICS_BEAT_DRY_RUN = True
    _metric_yesterday(tenant)

    with patch.object(tasks_mod, "aggregate_ai_metrics_daily") as live_task:
        result = aggregate_ai_metrics_daily_beat()

    live_task.apply.assert_not_called()
    assert result["mode"] == "dry_run"
    assert result["tenants"] == Tenant.objects.filter(is_active=True).count() >= 1
    assert result["rows"] == 1
    assert AIDailyMetricSummary.objects.count() == 0


def test_live_writes_yesterdays_summary_for_the_tenant(settings, tenant) -> None:
    settings.AI_METRICS_BEAT_ENABLED = True
    settings.AI_METRICS_BEAT_DRY_RUN = False
    _metric_yesterday(tenant)

    # _alert_breaches импортируется внутри задачи — патчить у источника.
    with patch("apps.observability.ai_metrics._alert_breaches", return_value=0):
        result = aggregate_ai_metrics_daily_beat()

    assert result["mode"] == "live"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    assert result["target_date"] == yesterday.isoformat()
    summary = AIDailyMetricSummary.objects.get(tenant=tenant, snapshot_date=yesterday)
    assert summary.total_requests == 1
