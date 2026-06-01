"""Django AppConfig for ``apps.orchestrator``.

Hosts the boot-time `ayla-ai-core` version smoke log per Block A9 in the
unified maintainability roadmap (`docs/architecture/unified-maintainability-roadmap.md`):

> A9 — ayla-ai-core SHA/version alignment (both consumers same SHA) +
> startup version smoke

Bot-platform and Ayla djangoproject are the two consumers of
ayla-ai-core; they MUST run the same SHA in production. The
boot-time log surfaces the resolved package version in container
stdout so a deploy operator can grep two log streams и verify the
SHAs match without shelling into either container.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class OrchestratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orchestrator"

    def ready(self) -> None:
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
        try:
            # PEP 610 — `direct_url.json` captures the install-time URL
            # including any `@<SHA>` suffix. When the package was
            # installed from PyPI (no direct URL), this read returns
            # None и we just log the version without source attribution.
            payload = dist.read_text("direct_url.json")
            if payload:
                import json

                data = json.loads(payload)
                url = data.get("url", "") or ""
                vcs_info = data.get("vcs_info", {}) or {}
                commit = vcs_info.get("commit_id", "") or ""
                if url and commit:
                    direct_url = f"{url}@{commit}"
                elif url:
                    direct_url = url
        except Exception:  # noqa: BLE001 — metadata read MUST NOT crash boot
            direct_url = ""

        logger.info(
            "orchestrator.ayla_ai_core.boot version=%s source=%s",
            version,
            direct_url or "<pypi-or-unknown>",
        )
