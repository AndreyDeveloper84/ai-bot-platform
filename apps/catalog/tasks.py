"""Catalog Celery tasks (DRF-579 / Sprint 7 / C5).

One periodic beat target — :func:`sync_catalog_for_all_tenants` —
that fans :class:`apps.catalog.services.sync.CatalogSyncService`
out across every active tenant.

### Why fan-out at the task layer (not inside the service)

The service is single-tenant by design (one Redis lock, one cursor,
one audit row per run). Sprint 8 multi-tenant ramp wants each tenant
to fail independently — one slow / broken tenant must not stall the
others. Doing the fan-out here gives us per-tenant try/except and
per-tenant scheduling granularity for Sprint 9+ when we may want to
stagger tenants by SLA tier.

### Beat schedule

Two entries in ``config.settings.base::CELERY_BEAT_SCHEDULE``:

* ``catalog_sync_every_15min`` → :func:`sync_catalog_for_all_tenants`.
  15-minute cadence, deliberately matched to the lock TTL ÷ 1.5 in
  :mod:`apps.catalog.services.sync` so a slow run can't race itself.
  (The key name here read ``catalog-sync-every-15min`` until DRF-1494;
  the real key uses underscores. A reader checking whether the sync was
  scheduled at all would have grepped the hyphenated name and found
  nothing.)
* ``catalog_sync_staleness_hourly`` → :func:`alert_stale_catalog_sync`.
  Hourly, offset to :07 so it reads a clock the :00/:15/:30/:45 sync has
  just had a chance to advance.

``apps/catalog/tests/test_beat_schedule.py`` pins both. Before DRF-1494
neither was covered: the sync could have been dropped from the schedule
by an unrelated edit and every test in the repository would still be
green.

### Soft time limit

Task carries a ``soft_time_limit=720`` (12 min) — under the 15-min
beat cadence so an overrun fires :class:`SoftTimeLimitExceeded`
in time for the next tick to start fresh. The service's Redis lock
TTL still guards against the worker silently hanging beyond that
window.

### Fan-out order (DRF-1595)

The order is ``last_catalog_sync_ok_at`` ascending, nulls first, then
``id``. Until DRF-1595 there was no ``order_by`` at all: the fan-out walked
``Tenant.objects.all()`` and took whatever order Postgres felt like
returning. That order is *stable*, which is exactly what made it dangerous —
when Ayla's rate limiter starts refusing partway through the walk, the same
salons are at the tail every single cycle. On the pilot that was
``formula-tela`` (the head salon) and ``fevralskiy-svet``: eight salons
synced minutes ago, those two had not synced in three days, and the bot
spent those three days telling clients that services the salon sells do not
exist. Ordering by staleness makes the loser of one cycle the first served
in the next; the ``id`` tie-break keeps that deterministic instead of
handing the decision back to the database whenever two clocks match.

### Wait budget (DRF-1595)

One :class:`~apps.catalog.services.throttle.ThrottleWaitBudget` per run,
shared by every tenant, capping the wall-clock this task may spend asleep
waiting out Ayla's ``429``s. Spent only on 429s — a healthy cycle never
touches it. When it runs out the fan-out stops issuing work and books the
remaining salons as *skipped*, which is deliberately not the same counter,
and not the same log event, as *failed*.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from django.db.models import F, QuerySet

from apps.catalog.services.sync import CatalogSyncService, SyncResult
from apps.catalog.services.throttle import ThrottleWaitBudget
from apps.catalog.staleness import sync_ages
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.observability.alerting import page
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.catalog.tasks.sync_catalog_for_all_tenants",
    soft_time_limit=720,
    time_limit=780,
)
def sync_catalog_for_all_tenants() -> dict[str, int]:
    """Beat target — sync every active tenant's catalog mirror.

    Returns:
      Counter dict ``{tenants_run, tenants_skipped,
      tenants_skipped_throttled, tenants_failed, total_created,
      total_updated, total_skipped, total_removed}``
      aggregated across the fan-out. The return value goes to the Celery
      result backend and nowhere else.

    Nothing watches this task's outcome from in here. Until DRF-1494 this
    docstring said the counters were "surfaced to ``check_agents`` health
    for 'last sync OK across N/M tenants'"; ``check_agents`` does not exist
    in this repository and never did. A sentence describing unbuilt
    monitoring is worse than no sentence, because it answers "are we
    watching this?" with a yes — and for twelve pilot days the answer was
    no. The watching is now done by :func:`alert_stale_catalog_sync` below,
    which reads the clock this fan-out advances instead of trusting a
    counter nobody collects.
    """
    counters = {
        "tenants_run": 0,
        "tenants_skipped": 0,
        # Subset of tenants_skipped: skipped because Ayla was rate-limiting us
        # and this run had no wait budget left (DRF-1595). Broken out because
        # "we stood down" and "it broke" are different facts and were, for
        # three pilot days, the same number in the same log line.
        "tenants_skipped_throttled": 0,
        "tenants_failed": 0,
        "total_created": 0,
        "total_updated": 0,
        "total_skipped": 0,
        "total_removed": 0,
    }

    budget = ThrottleWaitBudget.from_settings()
    service = CatalogSyncService(wait_budget=budget)
    stood_down: list[Tenant] = []
    for tenant in tenants_in_sync_order().iterator():
        # Skip the global_bot sentinel (#1019): it owns tenant-less global
        # BotUsers + discovery, NOT a salon catalog — the Ayla fetch with
        # its UUID returns 400 every cycle (perpetual tenants_failed=1 +
        # monitoring noise). Marker: GLOBAL_BOT_TENANT_SLUG from
        # apps.identity.constants — the same single source of truth the
        # identity resolver uses, so the sentinel can't drift past the
        # exclusion. The row itself is NOT touched (it must stay alive).
        #
        # Checked BEFORE the budget gate below so the sentinel never lands in
        # the stood-down list: it is not a salon and must not be reported as
        # a salon we failed to serve.
        if tenant.slug == GLOBAL_BOT_TENANT_SLUG:
            continue
        if budget.exhausted:
            # Stop issuing work rather than walking the rest and letting each
            # remaining salon burn one more fast-failing 429 (DRF-1595).
            # Continuing would deepen the hole we are climbing out of: every
            # request into an exhausted quota pushes back the moment the
            # limiter reopens, so the next tick would start in worse shape
            # than this one. Rotation is what makes standing down safe — these
            # salons are now the stalest, so the next cycle serves them first.
            stood_down.append(tenant)
            continue
        try:
            result = service.run(tenant)
        except Exception:
            counters["tenants_failed"] += 1
            logger.exception(
                "catalog.sync.beat_tenant_failed tenant_id=%s",
                tenant.id,
            )
            continue
        _accumulate(counters, result)

    if stood_down:
        counters["tenants_skipped"] += len(stood_down)
        counters["tenants_skipped_throttled"] += len(stood_down)
        logger.warning(
            "catalog.sync.beat_throttle_budget_exhausted budget=%ss spent=%ss "
            "stood_down=%d tenants=%s — Ayla rate-limited this run; these salons "
            "were NOT attempted and are NOT failures. They sort first next tick.",
            budget.total_seconds,
            budget.spent_seconds,
            len(stood_down),
            ",".join(t.slug for t in stood_down),
        )

    logger.info(
        "catalog.sync.beat_completed run=%s skipped=%s skipped_throttled=%s failed=%s "
        "created=%s updated=%s skipped_rows=%s removed=%s",
        counters["tenants_run"],
        counters["tenants_skipped"],
        # Separate field, not folded into `skipped`: an operator reading this
        # line has to be able to tell "Ayla is limiting us" from "a beat
        # raced itself on the lock" without opening the worker log.
        counters["tenants_skipped_throttled"],
        counters["tenants_failed"],
        counters["total_created"],
        counters["total_updated"],
        counters["total_skipped"],
        # Removal is the one destructive action a beat can take — it must not
        # be the only counter missing from the line an operator reads.
        counters["total_removed"],
    )
    return counters


def tenants_in_sync_order() -> "QuerySet[Tenant]":
    """Tenants ordered stalest-first — the fan-out's serving order.

    ``last_catalog_sync_ok_at`` ascending with NULLs first: a tenant that has
    never had a successful sync is the most behind there is, not the least,
    so it must not sort to the end (which is what an unqualified ASC does on
    Postgres, where NULLs are LAST by default).

    ``id`` is the tie-break and it is load-bearing, not decoration. Ties are
    ordinary — a fresh contour where several salons have never synced puts
    every one of them at NULL — and without a second key the database picks
    the order among them, which is precisely the failure DRF-1595 is about:
    an order nobody chose, stable enough to punish the same salons forever.
    ``id`` is the UUID primary key, so it is unique and the sort is total.

    A named function rather than an inline ``order_by`` on the loop: the
    order is the fix, and burying it in the ``for`` line is how it went
    missing in the first place.

    The tests that defend this
    (``apps/catalog/tests/test_beat_order_budget_drf1595.py``) assert the
    order salons are actually SERVED in, not this queryset's shape — nothing
    about the old code was malformed, it simply had no opinion, so only
    behaviour can tell a deliberate tie-break from a lucky one.
    """
    return Tenant.objects.order_by(F("last_catalog_sync_ok_at").asc(nulls_first=True), "id")


def _accumulate(counters: dict[str, int], result: SyncResult) -> None:
    """Roll a per-tenant :class:`SyncResult` into the fan-out totals."""
    if result.skipped:
        counters["tenants_skipped"] += 1
        if result.skip_reason == "throttled":
            counters["tenants_skipped_throttled"] += 1
        return
    if not result.ran or result.error:
        counters["tenants_failed"] += 1
        return
    counters["tenants_run"] += 1
    # All three mirrors count toward the beat totals (DRF-945). The masters
    # mirror landed on dev without being wired in here; folding it and the new
    # bookable-edge mirror in together keeps "last sync OK across N/M tenants"
    # honest — otherwise a mirror could fail every cycle and the beat counters
    # would still look clean.
    for mirror in (result.services, result.masters, result.master_services):
        counters["total_created"] += mirror.created
        counters["total_updated"] += mirror.updated
        counters["total_skipped"] += mirror.skipped
        counters["total_removed"] += mirror.removed


@shared_task(
    name="apps.catalog.tasks.alert_stale_catalog_sync",
    soft_time_limit=60,
    time_limit=90,
)
def alert_stale_catalog_sync() -> dict[str, int]:
    """Page the on-call channel when a tenant's catalog stopped refreshing.

    Reads ``Tenant.last_catalog_sync_ok_at`` — the wall-clock
    :class:`~apps.catalog.services.sync.CatalogSyncService` stamps on every
    run that got past the Ayla fetch — and pages once per hour per stale
    tenant through :func:`apps.observability.alerting.page`, which fans out
    to the operators' Telegram channel and to Sentry.

    ### Severity, and why ``error`` rather than ``warning``

    ``warning`` is delivered muted (see the severity matrix in
    ``apps.observability.alerting``). A muted channel is the correct place
    for capacity headroom and is the wrong place for this: while the mirror
    is stale the bot is not degraded, it is *confidently wrong* — it tells
    clients that services the salon actively sells do not exist. That is a
    revenue-losing answer given in the salon's own voice, so it gets the
    unmuted line.

    ### Cadence, and the noise trade

    Hourly, not per-sync-cycle. ``page()`` dedups on a five-minute TTL, so
    a check running on the 15-minute sync cadence would put four lines an
    hour per stale salon into the channel — and an operator who mutes the
    channel is the state this whole ticket exists to prevent. Hourly costs
    up to an extra hour of detection latency on top of the one-hour
    threshold. Two hours against the twelve days this replaces is the right
    side of that trade.

    Returns:
      ``{"checked": N, "stale": M, "paged": P}``. ``paged`` can be lower
      than ``stale`` when :func:`page` dedups or every sink is unreachable;
      it is the honest count of alerts that left the process, not of alerts
      we would have liked to send.
    """
    ages = sync_ages()
    stale = [age for age in ages if age.is_stale]
    paged = 0

    for age in stale:
        sent = page(
            "error",
            f"Каталог салона {age.slug} не синхронизировался {age.age_human}",
            (
                f"tenant={age.slug} (id={age.tenant_id})\n"
                f"last successful catalog sync: "
                f"{age.last_ok_at.isoformat() if age.last_ok_at else 'never'}\n"
                f"age: {age.age_human} (threshold "
                f"{age.threshold_seconds // 60}m)\n\n"
                "Пока это длится, бот отвечает клиентам по устаревшему каталогу и "
                "говорит «такого у наших мастеров нет» про услуги, которые салон "
                "продаёт.\n"
                "Причину искать в логах воркера по catalog.sync.fetch_failed / "
                "catalog.http.row_unparseable для этого tenant_id.\n"
                "Разовый прогон: админка → каталог → действие "
                "«Пересинхронизировать каталог выбранных салонов» (DRF-1581), "
                "либо manage.py sync_catalog --tenant " + age.slug
            ),
            dedup_key=f"catalog_sync_stale:{age.slug}",
        )
        if sent:
            paged += 1

    logger.info(
        "catalog.sync.staleness_checked checked=%d stale=%d paged=%d",
        len(ages),
        len(stale),
        paged,
    )
    return {"checked": len(ages), "stale": len(stale), "paged": paged}


@shared_task(
    name="apps.catalog.tasks.sync_catalog_for_tenant",
    soft_time_limit=720,
    time_limit=780,
)
def sync_catalog_for_tenant(tenant_id: str) -> dict[str, Any]:
    """Manual force-resync for ONE tenant — the DRF-1581 admin button's payload.

    ### Why a separate task instead of reusing the beat fan-out

    The admin action (``CatalogServiceAdmin.force_resync_selected_tenants``)
    enqueues this per selected tenant. Calling
    :func:`sync_catalog_for_all_tenants` from the button would sync every
    salon on one click: Ayla's internal catalog is anonymously rate-limited
    (~30 req/min, measured by the DRF-1595 window), and a full fan-out
    would throttle itself exactly while the operator is firefighting one
    stale salon.

    ### Idempotency — the service lock is the guard

    :meth:`CatalogSyncService.run` runs under a per-tenant Redis advisory
    lock and skips (does NOT queue) when a run is already in flight. A
    second click while the first run holds the lock comes back here as
    ``skipped=True`` — double-clicking the button cannot start two syncs
    of the same salon.

    Returns:
      ``{"tenant", "ran", "skipped", "error", "services_created",
      "services_updated", ...}`` — goes to the Celery result backend.
      The operator-facing confirmation is the admin action's message;
      completion is visible by the ``synced_at`` column moving.
    """
    tenant = Tenant.objects.get(id=UUID(str(tenant_id)))
    result = CatalogSyncService().run(tenant)
    outcome: dict[str, Any] = {
        "tenant": tenant.slug,
        "ran": result.ran,
        "skipped": result.skipped,
        "error": result.error,
    }
    for name, mirror in (
        ("services", result.services),
        ("masters", result.masters),
        ("edges", result.master_services),
    ):
        outcome[f"{name}_created"] = mirror.created
        outcome[f"{name}_updated"] = mirror.updated
        outcome[f"{name}_removed"] = mirror.removed
    logger.info(
        "catalog.sync.manual_run tenant=%s ran=%s skipped=%s error=%s",
        tenant.slug,
        result.ran,
        result.skipped,
        result.error or "-",
    )
    return outcome


@shared_task(
    name="apps.catalog.tasks.sweep_schedule_confirmations",
    soft_time_limit=300,
    time_limit=360,
)
def sweep_schedule_confirmations() -> dict[str, int]:
    """Снять подтверждения, под которыми часы уже изменились. §83, правило 4.

    **Это единственный работающий механизм сброса, а не запасной путь.**

    Правило 4 решения владельца звучит как «любое изменение рабочих часов
    автоматически отменяет подтверждение», и правильным местом для него
    было бы событие ``master.schedule.updated``: контракт §3.11 его
    описывает, консьюмер (``apps.eventbus.consumers.schedule``) его
    принимает и сброс делает. Замер пилота 09.09.2026 показал, что **этот
    топик не приходил ни разу за всю историю** — ``IngestDedupe`` знает
    только ``booking.*`` и два ``appointment.rescheduled``. Регистрация
    консьюмера доказывает готовность принять, а не факт приёма.

    **И причин две, независимых.** Даже будь событие отправлено, оно было
    бы отвергнуто: живое значение аллоулиста на пилоте (прочитано в
    контейнере бота 09.09.2026) — ``EVENT_INGEST_ALLOWED_EVENTS`` несёт
    ровно четыре имени, все ``booking.*`` плюс ``appointment.rescheduled``.
    ``master.schedule.updated`` среди них нет, и событие ушло бы в мёртвую
    очередь с ``event_not_allowed``. Состав аллоулиста — решение владельца
    (OD-T02-5), а не наша забывчивость, и меняется правкой окружения, а не
    этим кодом.

    Поэтому обход — не дубль событийного пути и не подстраховка к нему:
    **на 09.09.2026 другого работающего пути инвалидации нет вовсе.**

    Дата здесь несущая, а не оформление. Обе причины снимаются правкой
    окружения и рестартом — то есть без единой строки кода, — и в тот день
    это утверждение станет неверным, **не изменившись ни буквой**. В диффе
    такого не видно никому и никогда, поэтому у него три способа
    перепроверки, от дешёвого к дорогому:

    1. **загрузочный лог процесса.** ``observability.W010`` (сторож
       DRF-1391, ``apps/observability/checks.py``) пишет
       ``env_file_drift``, когда env-файл объявляет ключ, а процесс его не
       получил. Пока строка про ``EVENT_INGEST_ALLOWED_EVENTS`` там есть —
       файл поправлен, а процесс держит прежнее, и событийный путь закрыт.
       Исчезла — контейнеры пересозданы. Рядом в том же логе видно и
       ``allowlist_active`` с фактическим составом, и
       ``eventbus.ingest.handler_registered name=master.schedule.updated``:
       обе половины утверждения читаются из одного старта;
    2. ``EVENT_INGEST_ALLOWED_EVENTS`` внутри контейнера;
    3. ``IngestDedupe`` по имени топика — приходило ли оно хоть раз.

    Если событийный путь открыт, обход остаётся страховкой, а не
    единственным механизмом, и эту фразу надо переписать.

    Поэтому обход, и поэтому цену надо назвать честно:

        правило 4 исполняется как «отменяет НЕ ПОЗДНЕЕ ЧЕМ через цикл»,
        а не «отменяет в момент изменения».

    Между изменением часов в Ayla и сбросом проходит до одного интервала
    бита. В это окно мастер продаётся по часам, которые владелица
    подтверждала не глядя на нынешние. Выдавать обход за мгновенный сброс
    нельзя — свойства у него такого нет.

    ### Обход сторожит СУЩЕСТВУЮЩИЕ подтверждения

    Проверяются только строки с ``schedule_confirmed_at IS NOT NULL``: у
    неподтверждённого мастера сбрасывать нечего. Цена — один HTTP-вызов на
    ПОДТВЕРЖДЁННОГО мастера за проход, а не на весь пул подбора: сегодня
    это максимум девять строк из тридцати одной (замер 09.09.2026).

    **Обратного направления у обхода нет, и это названо, а не забыто.**
    Он замечает, что у подтверждённого часы изменились, и НЕ замечает, что
    у неподтверждённого они появились. Мастер, которому салон завёл часы в
    Ayla, подтверждаемым сам собой не станет и никого об этом не уведомит.
    Сегодня таких потенциально двадцать два. Это дыра продуктового
    сценария, а не дефект механизма; если понадобится уведомление, оно
    сядет сюда же.

    ### Недоступность источника — НЕ изменение часов

    Ayla не ответила — подтверждение остаётся, строка считается в
    ``unreadable`` и называется в логе. Обратное решение (нет ответа →
    сбросить) сняло бы с витрины всех подтверждённых разом на первой же
    сетевой ошибке, и владелица прочла бы это как «у всех изменилось
    расписание». Отсутствие ответа доезжает отсутствием.

    Возвращает счётчики прохода: ``checked`` / ``cleared`` / ``unreadable``.
    """

    from apps.catalog.models import CatalogMaster
    from apps.catalog.services.schedule_confirmation import (
        ScheduleConfirmationError,
        clear_confirmation,
        read_weekly_template,
    )
    from apps.integrations.ayla.salon_client import SalonNotConfigured, SalonUnavailable
    from apps.tenancy.context import tenant_scope

    counters = {"checked": 0, "cleared": 0, "unreadable": 0}

    for tenant in tenants_in_sync_order():
        with tenant_scope(tenant):
            # ``objects`` внутри скоупа, а не ``all_tenants``: межсалонное
            # чтение каталога живёт только в ``apps/marketplace`` (MKT1), и
            # обход — не повод его завести.
            confirmed = list(
                CatalogMaster.objects.filter(schedule_confirmed_at__isnull=False).select_related(
                    "tenant"
                )
            )
            for master in confirmed:
                counters["checked"] += 1
                try:
                    template = read_weekly_template(master)
                except (ScheduleConfirmationError, SalonNotConfigured, SalonUnavailable) as exc:
                    counters["unreadable"] += 1
                    logger.warning(
                        "catalog.schedule_sweep.unreadable master=%s tenant=%s err=%s — "
                        "подтверждение оставлено: нет ответа источника, а не изменение часов",
                        master.pk,
                        tenant.id,
                        type(exc).__name__,
                    )
                    continue

                if template.fingerprint == master.schedule_fingerprint:
                    continue

                counters["cleared"] += clear_confirmation(
                    tenant_id=tenant.id,
                    ayla_user_id=master.ayla_user_id,
                    reason="sweep: hours changed since confirmation",
                )

    logger.info(
        "catalog.schedule_sweep.done checked=%d cleared=%d unreadable=%d",
        counters["checked"],
        counters["cleared"],
        counters["unreadable"],
    )
    return counters
