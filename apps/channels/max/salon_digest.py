"""«Утренний итог» салонного бота — тип 6 уведомлений-решений (DRF-2118, §50 п.8).

Владельцу/админу салона раз в день, в час салона, приходит та же сводка,
что и в приветствии при входе (DRF-2114): «сегодня N записей; работают M
мастеров; ситуаций K» или спокойная строка. **Один источник**: строки
собирает :func:`apps.channels.max.salon_greeting.gather` и рендерит
:func:`apps.channels.max.salon_greeting.render_summary_lines` — здесь нет
своей копии подсчётов.

Beat — по образцу ``nutrition_proactive.send_daily_reports``: тик каждый
час (``crontab(minute="50")``), планировщик сверяет местный час каждого
салона и отбрасывает остальные 23 тика; выключатель
``SALON_MORNING_DIGEST_ENABLED`` (умолчание False — включение на стенде
через env, не деплой).

**Час салона** — ``Tenant.features["morning_digest_hour"]`` (0–23), иначе
:data:`DEFAULT_HOUR` = 09:00 по ``Tenant.timezone``. Названный предел:
отдельной настройки и экрана для часа нет, оператор правит JSON
``features`` в админке; до этого — 09:00 у всех.

**Ночью не слать**: тихих часов у рабочего бота нет, но до
:data:`NIGHT_BEFORE_HOUR` (07:00) местного итог не уходит даже при
настроенном часе — ``night``.

**Квота 1/день** — дедуп ``salon_notify`` по ``(tenant, местная дата)``:
второй тик в ту же дату → ``already_sent_today``. Дата — в поясе салона,
не UTC. Пределы дедупа — те же, что у остальных типов (``cache.add``).

**Отказ источников**: часть — строка опускается, как в приветствии;
все — итог не уходит (``source_failed``) и день НЕ считается
отправленным: следующий тик попробует снова.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

DEFAULT_HOUR = 9
NIGHT_BEFORE_HOUR = 7
FEATURE_KEY = "morning_digest_hour"

REASONS = (
    "send",
    "not_digest_hour",
    "night",
    "no_recipients",
    "already_sent_today",
    "source_failed",
)


@dataclass(frozen=True)
class Decision:
    tenant_slug: str
    send: bool
    reason: str
    local_hour: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def enabled() -> bool:
    return bool(getattr(settings, "SALON_MORNING_DIGEST_ENABLED", False))


def digest_hour(tenant: Any) -> int:
    """Час итога по салону: ``features[morning_digest_hour]`` (0–23), иначе 09:00."""

    features = getattr(tenant, "features", None) or {}
    raw = features.get(FEATURE_KEY) if isinstance(features, dict) else None
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 23:
        return raw
    if raw is not None:
        logger.warning(
            "channels.max.salon_digest.bad_hour tenant=%s value=%r — беру %d",
            getattr(tenant, "slug", "-"),
            raw,
            DEFAULT_HOUR,
        )
    return DEFAULT_HOUR


def local_now(tenant: Any, now_utc: datetime) -> datetime:
    try:
        return now_utc.astimezone(ZoneInfo(getattr(tenant, "timezone", "") or "Europe/Moscow"))
    except Exception:  # noqa: BLE001 — незнакомый пояс: Москва, как у приветствия по умолчанию
        return now_utc.astimezone(ZoneInfo("Europe/Moscow"))


class _AdminRole:
    """Роль-стаб для ``gather``: сводка владельца (с готовностью), без мастерских строк."""

    is_owner = True
    is_admin = False
    is_master = False
    is_receptionist = False


def gather_digest(tenant: Any, *, now: datetime | None = None):
    """Сводка владельца тем же ``salon_greeting.gather`` — не копией."""

    from apps.channels.max import salon_greeting

    return salon_greeting.gather(tenant, _AdminRole(), now=now)


def _candidates():
    from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
    from apps.tenancy.models import Tenant

    return Tenant.objects.exclude(slug=GLOBAL_BOT_TENANT_SLUG).order_by("slug")


def plan_morning_digests(*, now_utc: datetime | None = None) -> list[Decision]:
    """Решение по каждому салону на этот тик; отправка — в :func:`send_morning_digests`."""

    from apps.channels.max.staff_outbound import manager_recipients

    now_utc = now_utc or dj_timezone.now()
    decisions: list[Decision] = []
    for tenant in _candidates():
        local = local_now(tenant, now_utc)
        hour = digest_hour(tenant)

        def decide(reason: str, *, send: bool = False, **detail: Any) -> Decision:
            return Decision(tenant.slug, send, reason, local.hour, dict(detail))

        if local.hour < NIGHT_BEFORE_HOUR:
            decisions.append(decide("night", wanted_hour=hour))
            continue
        if local.hour != hour:
            decisions.append(decide("not_digest_hour", wanted_hour=hour))
            continue
        if not manager_recipients(tenant):
            decisions.append(decide("no_recipients"))
            continue
        decisions.append(decide("send", send=True, local_date=local.date().isoformat()))
    return decisions


def _deliver(tenant: Any, local: datetime) -> str:
    """Собрать и отправить итог одному салону; вернуть reason."""

    from apps.channels.max import salon_greeting, salon_notify

    data = gather_digest(tenant, now=local)
    lines = salon_greeting.render_summary_lines(tenant.name, data)
    if not lines:
        # Все источники отказали — пустой «Итог» хуже молчания, и день не
        # засчитывается: ключ дедупа не ставится, следующий тик попробует.
        logger.warning(
            "channels.max.salon_digest.source_failed tenant=%s missing=%s",
            tenant.slug,
            ",".join(data.missing) or "-",
        )
        return "source_failed"
    result = salon_notify.notify(
        salon_notify.digest_notice(tenant, local_date=local.date(), lines=lines)
    )
    if result is None:
        return "already_sent_today"
    return "send"


@shared_task(name="salon_notify.send_morning_digests")
def send_morning_digests(now_utc: datetime | str | None = None) -> dict[str, int]:
    """Beat: раз в час; шлёт итог салонам, у которых сейчас их час. Никогда не бросает."""

    if not enabled():
        return {"disabled": 1}
    if isinstance(now_utc, str):
        now_utc = datetime.fromisoformat(now_utc)
    now_utc = now_utc or dj_timezone.now()

    from apps.tenancy.models import Tenant

    counters: dict[str, int] = {reason: 0 for reason in REASONS}
    counters["sent"] = 0
    for decision in plan_morning_digests(now_utc=now_utc):
        if not decision.send:
            counters[decision.reason] += 1
            continue
        tenant = Tenant.objects.filter(slug=decision.tenant_slug).first()
        if tenant is None:
            counters["no_recipients"] += 1
            continue
        try:
            outcome = _deliver(tenant, local_now(tenant, now_utc))
        except Exception:  # noqa: BLE001 — один салон не отменяет остальных
            logger.exception("channels.max.salon_digest.failed tenant=%s", decision.tenant_slug)
            outcome = "source_failed"
        if outcome == "send":
            counters["sent"] += 1
        else:
            counters[outcome] += 1
    counters.pop("send", None)
    logger.info(
        "channels.max.salon_digest.tick %s",
        " ".join(f"{k}={v}" for k, v in sorted(counters.items())),
    )
    return counters


__all__ = [
    "DEFAULT_HOUR",
    "Decision",
    "FEATURE_KEY",
    "NIGHT_BEFORE_HOUR",
    "REASONS",
    "digest_hour",
    "enabled",
    "gather_digest",
    "local_now",
    "plan_morning_digests",
    "send_morning_digests",
]
