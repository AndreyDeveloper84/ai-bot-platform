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
