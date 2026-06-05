"""Django AppConfig for ``apps.orchestrator``.

Hosts the boot-time `ayla-ai-core` version smoke log per Block A9 in the
unified maintainability roadmap (`docs/architecture/unified-maintainability-roadmap.md`):

> A9 — ayla-ai-core SHA/version alignment (both consumers same SHA) +
> startup version smoke

Bot-platform and Ayla djangoproject are the two consumers of
ayla-ai-core; they MUST run the same SHA in production. The
boot-time log surfaces the resolved package version in container
stdout so a deploy operator can grep two log streams and verify the
SHAs match without shelling into either container.

The log is intentionally suppressed for short-lived management
invocations (`pytest`, `migrate`, `collectstatic`, `makemigrations`,
`check`, `shell`, `createsuperuser`, `compilemessages`,
`makemessages`) — see `_should_emit_boot_log()` — so ops grep
against the production stream is not diluted by CI/build-time noise.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Adversarial CR M2 (PR #935, 2026-06-01) — management commands that
# launch ``AppConfig.ready()`` but are NOT long-running production
# workloads. The boot log adds nothing useful here and clutters CI /
# build / shell sessions с structured-JSON line noise.
_QUIET_MANAGEMENT_ARGS = frozenset(
    {
        "pytest",
        "migrate",
        "makemigrations",
        "collectstatic",
        "check",
        "shell",
        "shell_plus",
        "createsuperuser",
        "compilemessages",
        "makemessages",
        "showmigrations",
        "sqlmigrate",
        "test",
        "diffsettings",
        "dbshell",
        "dumpdata",
        "loaddata",
        "inspectdb",
        "flush",
        "graph_models",
    }
)


def _should_emit_boot_log() -> bool:
    """Adversarial CR M2 — gate the boot log to runtime invocations.

    Returns False when:
    - ``AYLA_BOOT_LOG_SUPPRESS`` env var is set (escape hatch for ops).
    - pytest is running (basename of argv[0] starts with ``pytest``).
    - manage.py is invoking a quiet management command per
      ``_QUIET_MANAGEMENT_ARGS``.

    Returns True for ``runserver``, ``runworker``, ``runuvicorn``,
    gunicorn / uvicorn / wsgi launches, or any unrecognised entry
    point (default-emit so we don't accidentally silence production).
    """
    if os.environ.get("AYLA_BOOT_LOG_SUPPRESS"):
        return False

    argv = sys.argv or []
    if not argv:
        return True

    entry = os.path.basename(argv[0] or "").lower()
    if entry.startswith("pytest") or "pytest" in entry:
        return False

    if len(argv) >= 2:
        cmd = argv[1].lower()
        if cmd in _QUIET_MANAGEMENT_ARGS:
            return False

    return True


class OrchestratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orchestrator"

    def ready(self) -> None:
        if _should_emit_boot_log():
            self._log_ayla_ai_core_version()

    @staticmethod
    def _log_ayla_ai_core_version() -> None:
        """Emit a startup INFO line with the resolved ``ayla-ai-core``
        package version + source URL.

        Pulled from `importlib.metadata` so the value matches what
        `pip` / `uv` actually resolved (not a hardcoded string in the
        codebase that might drift from `uv.lock`). The optional
        `Direct-URL` PEP 610 metadata carries the @SHA pinning suffix
        when the package was installed from git — we surface it so
        ops can compare against Ayla djangoproject's boot log.

        Silent when the package is not installed — non-AI contributors
        running `uv sync --extra dev` (without `--extra ai-core`) MUST
        NOT see a confusing WARN on boot.
        """
        try:
            from importlib.metadata import PackageNotFoundError, distribution
        except ImportError:  # pragma: no cover — stdlib on supported Pythons
            return

        try:
            dist = distribution("ayla-ai-core")
        except PackageNotFoundError:
            return

        version = dist.version
        direct_url = ""
        # Adversarial CR H2 — narrow exception types и emit DEBUG
        # breadcrumb instead of silently attributing corrupt metadata as
        # «pypi-or-unknown». Ops grepping two log streams для drift will
        # otherwise misread corrupt-direct_url-on-one-side as
        # «both containers on PyPI».
        try:
            payload = dist.read_text("direct_url.json")
        except OSError as exc:
            logger.debug(
                "orchestrator.ayla_ai_core.direct_url_read_failed err=%s",
                exc,
            )
            payload = None

        if payload:
            try:
                data = json.loads(payload)
            except (ValueError, UnicodeDecodeError) as exc:
                logger.warning(
                    "orchestrator.ayla_ai_core.direct_url_corrupt err=%s payload_size=%d",
                    exc,
                    len(payload),
                )
                data = {}
            url = data.get("url", "") or ""
            vcs_info = data.get("vcs_info", {}) or {}
            commit = vcs_info.get("commit_id", "") or ""
            if url and commit:
                direct_url = f"{url}@{commit}"
            elif url:
                direct_url = url

        logger.info(
            "orchestrator.ayla_ai_core.boot version=%s source=%s",
            version,
            direct_url or "<pypi-or-unknown>",
        )
