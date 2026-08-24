"""Deploy-time guard for ``SITE_DOMAIN`` (DRF-1079).

### What went wrong without it

``SITE_DOMAIN`` has a repository default of ``http://localhost:5173``
(``config/settings/base.py:537``) and is named in no env template. On
the pilot it was never set, so every master invite carried a web
fallback to a port on a machine the invited master does not own —
verified in the running container, the variable is absent entirely.

Nothing complained. The endpoint answered 201, the DM went out, the
audit row said ``delivery=queued``. The only observer of the defect was
the master, who has no way to report «this link opens nothing» to
anyone who could act on it.

### Why a check and not a fail-fast

``config/settings/production.py`` raises ``ImproperlyConfigured`` for
missing secrets, but the pilot runs ``config.settings.staging`` and a
raise there would refuse to boot a contour that is otherwise healthy —
trading a broken invite link for a dead bot. A system check is the
right weight: ``manage.py migrate`` and ``manage.py check`` run it, so
it is loud in CI and in the deploy log, and it blocks nothing.

Registered from :class:`apps.admin_api.apps.AdminApiConfig` — the app
that owns the endpoint embedding the value.
"""

from __future__ import annotations

from typing import Any

from django.core.checks import Warning as CheckWarning


def check_site_domain(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """Warn when the master-invite web fallback would point at localhost."""

    from django.conf import settings

    from apps.admin_api.views_invite import _site_domain, _site_domain_is_loopback

    if settings.DEBUG:
        return []
    if not _site_domain_is_loopback():
        return []
    return [
        CheckWarning(
            "SITE_DOMAIN is not set — master invite links point at "
            f"{_site_domain()}, which resolves on the developer machine "
            "and nowhere else.",
            hint=(
                "Set SITE_DOMAIN to the Mini App origin (pilot: "
                "https://api-dev.gobeauty.site). Until then the web "
                "fallback is suppressed and an invited master has only "
                "the in-MAX deeplink."
            ),
            id="admin_api.W001",
        )
    ]
