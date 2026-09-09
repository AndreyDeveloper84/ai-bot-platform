"""Startup guards that make silent configuration drift audible (DRF-1391).

Two checks, one subject: what the operator declared vs what the process
actually got.

``observability.W010`` — env-file drift. The computation lives in
:mod:`config.env_file_drift`, next to the full account of the pilot
failure that produced it.

``observability.W011`` — ``ALLOWED_HOSTS`` is a wildcard on a contour that
is not in ``DEBUG``. This is the specific state DRF-1391 found on the
pilot, and W010 alone would not report it: once the base compose override
is gone, a `*` written *into* an env file agrees with the process
perfectly and drifts from nothing.

### Why warnings and not errors

``manage.py migrate`` runs system checks and aborts on ``ERROR``, and
``.github/workflows/deploy-dev.yml`` runs ``migrate`` between build and
restart. An ``ERROR`` here would therefore convert a config smell into a
failed deploy on a contour that is otherwise serving traffic — the same
trade `apps/admin_api/checks.py` declined for ``SITE_DOMAIN``, for the
same reason. Promotion is a one-word change (``CheckWarning`` →
``CheckError``) if the owner decides the wildcard should block a deploy.

### Why also logged from ``ready()``

System checks run under ``manage.py``. The pilot's ``web`` service is
``uvicorn config.asgi:application``, which never invokes them — so a
check alone would have stayed invisible in exactly the process that
mattered. ``ObservabilityConfig.ready()`` runs in every process, uvicorn
included, and logs the same findings at WARNING.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.checks import Warning as CheckWarning

logger = logging.getLogger(__name__)

ENV_DRIFT_CHECK_ID = "observability.W010"
WILDCARD_HOSTS_CHECK_ID = "observability.W011"
OUTBOX_BACKLOG_CHECK_ID = "observability.W012"


def _drift_findings() -> list[Any]:
    from django.conf import settings

    from config.env_file_drift import compute_env_file_drift, resolve_drift_paths

    paths = resolve_drift_paths(settings.BASE_DIR)
    if not paths:
        return []
    return compute_env_file_drift(paths)


def check_env_file_drift(app_configs: Any = None, **kwargs: Any) -> list[CheckWarning]:
    """Report keys an env file declares that the process did not receive."""

    findings = _drift_findings()
    if not findings:
        return []

    keys = ", ".join(sorted(f.describe() for f in findings))
    return [
        CheckWarning(
            "Environment drift: the running process disagrees with the env "
            f"file about {len(findings)} variable(s): {keys}.",
            hint=(
                "Something between the env file and the process replaced "
                "these values — on compose, a service's `environment:` "
                "mapping beats its `env_file:`, and mappings merge across "
                "`-f` files key by key, so a value in docker-compose.yml "
                "silently outranks docker-compose.staging.yml's env_file. "
                "Run `docker compose -f docker-compose.yml "
                "-f docker-compose.staging.yml config` and look for these "
                "keys under the service's `environment:`. Values are "
                "withheld here on purpose — these files hold secrets."
            ),
            id=ENV_DRIFT_CHECK_ID,
        )
    ]


def check_allowed_hosts_not_wildcard(app_configs: Any = None, **kwargs: Any) -> list[CheckWarning]:
    """Report ``ALLOWED_HOSTS = ['*']`` outside DEBUG.

    A wildcard turns off Django's Host-header validation, which is what
    makes ``request.get_host()`` trustworthy. Absolute URLs built from it
    (master invite links, payment return URLs, password-reset mails) then
    carry whatever host the caller asked for, and any cache in front of
    the app can be keyed on a host the operator never configured.
    """

    from django.conf import settings

    if settings.DEBUG:
        return []
    if "*" not in list(settings.ALLOWED_HOSTS):
        return []
    return [
        CheckWarning(
            "ALLOWED_HOSTS contains '*' with DEBUG=False — Django accepts a "
            "request with ANY Host header on a contour that is not local dev.",
            hint=(
                "Set DJANGO_ALLOWED_HOSTS to the names that actually reach "
                "this process. On the pilot that is "
                "api-dev.gobeauty.site,localhost,127.0.0.1 — the public "
                "nginx vhost, the container's own healthcheck "
                "(`curl http://localhost:8000/healthz/`), and the host-side "
                "deploy probe (`curl http://127.0.0.1:8013/readyz/`). "
                "Dropping either loopback name turns a healthy contour red."
            ),
            id=WILDCARD_HOSTS_CHECK_ID,
        )
    ]


def log_startup_config_drift() -> None:
    """Emit the same findings as a log line, for processes that skip checks.

    Best-effort by construction: a reporter that can abort a boot is a
    reporter that can take a contour down, and this one exists precisely
    because the contour was up the whole time.
    """

    try:
        for finding in _drift_findings():
            logger.warning(
                "env_file_drift: %s declared in %s did not reach the process (%s)",
                finding.key,
                finding.path.name,
                finding.kind,
            )
        for warning in check_allowed_hosts_not_wildcard():
            logger.warning("%s: %s", warning.id, warning.msg)
    except Exception:  # pragma: no cover - never let a reporter break boot
        logger.exception("env_file_drift: startup drift report failed")


def check_outbox_backlog(app_configs: Any = None, **kwargs: Any) -> list[CheckWarning]:
    """Сообщить, что исходящий ящик не разбирается.

    Предупреждение, а не ошибка, по той же причине, что у W010/W011:
    ``manage.py migrate`` прогоняет системные проверки и падает на
    ``ERROR``, а `deploy-dev.yml` зовёт `migrate` между сборкой и
    рестартом. Неразобранный ящик — состояние, в котором контур
    обслуживает людей; превращать его в несостоявшуюся выкладку значит
    менять одну беду на другую. Повышение — правка одного слова.

    ### Почему ЗДЕСЬ отказ БД — молчание, а в ``ready()`` — крик

    Разница не в осторожности, а в том, что означает отказ в каждом месте.

    Системные проверки идут там, где базы законно может не быть: под
    ``manage.py migrate`` до применения миграций, на свежем чекауте, в CI
    между `uv sync` и первым прогоном. «Таблицы нет» здесь — не факт об
    исходящем ящике, а факт о том, что приложение ещё не мигрировано, и
    говорить о ящике нечего. Предупреждение на каждом таком запуске
    научило бы читателя пролистывать W012 — то есть погасило бы сторожа
    ровно тогда, когда он однажды скажет правду.

    В ``ready()`` работающего процесса тот же отказ означает другое: БД
    недоступна там, где без неё не обслужить ни одного запроса. Поэтому
    :func:`log_outbox_backlog` его НАЗЫВАЕТ — с причиной и на ``WARNING``.

    Итог: тишина здесь не оставляет отказ безымянным — имя ему даёт
    другой рупор, в том процессе, где отказ является новостью.
    """

    from django.db import DatabaseError

    from apps.observability.outbox_backlog import STALE_AFTER, measure_outbox_backlog

    try:
        backlog = measure_outbox_backlog()
    except DatabaseError:
        return []

    if not backlog.is_stale:
        return []

    return [
        CheckWarning(
            f"Исходящий ящик не разбирается: {backlog.describe()}. Порог — {STALE_AFTER}.",
            hint=(
                "Задача `apps.eventbus.dispatch_pending_events` объявлена, но "
                "её нет в CELERY_BEAT_SCHEDULE, и вызвать её больше неоткуда "
                "(docs/PILOT_MEASUREMENTS.md §11). Порядок починки — счётчик, "
                "затем подписчик, затем расписание: `NoopSubscriber` не бросает "
                "исключений, поэтому диспетчер, запущенный раньше подписчика, "
                "пометит накопленное доставленным никому, и обратного хода нет."
            ),
            id=OUTBOX_BACKLOG_CHECK_ID,
        )
    ]


def log_outbox_backlog() -> None:
    """Тот же замер строкой в лог — для процессов, что не гоняют проверки.

    Три исхода, и все три различимы **уровнем**, а не только текстом:

    * ящик разбирается (пуст или молод) — ``INFO``;
    * ящик не разбирается — ``WARNING`` с числами;
    * **посчитать не удалось** — ``WARNING`` с причиной.

    Третий случай назван отдельно намеренно. Молчание при отказе сторожа
    неотличимо от молчания при пустом ящике — это ровно тот дефект,
    против которого сторож и заведён, только этажом выше. Отсюда и
    разные уровни: отфильтровав лог по ``WARNING``, оператор обязан
    увидеть отказ, а не потерять его вместе с рутинным «пусто».

    Причина обязательна: «не смог посчитать» без неё — то же молчание,
    только длиннее. Ошибка при этом глотается: сторож, способный уронить
    загрузку, — это авария, которую он же и должен был предотвращать.
    """

    try:
        from apps.observability.outbox_backlog import measure_outbox_backlog

        backlog = measure_outbox_backlog()
    except Exception as exc:  # noqa: BLE001 — reporter must never break boot
        # Форма взята у `apps.workers.subscriber_audit`: причина + прямое
        # признание, что данные неполны. «Ноль» здесь сказать нельзя —
        # мы его не измерили.
        logger.warning(
            "outbox_backlog.not_measured reason=%s — boot continues, "
            "backlog unknown for this process",
            f"{type(exc).__name__}: {exc}"[:200],
        )
        return

    if backlog.is_stale:
        logger.warning("outbox_backlog.stale %s", backlog.describe())
    else:
        logger.info("outbox_backlog.ok %s", backlog.describe() if backlog.pending else "ящик пуст")
