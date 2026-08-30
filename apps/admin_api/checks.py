"""Deploy-time guards for the master-invite entry (DRF-1079, DRF-1349).

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

### The hint is imported, not retyped

The wording lives in ``views_invite.SITE_DOMAIN_HINT``. Both copies of
this hint used to be written out by hand and both named the *backend*
host ``api-dev.gobeauty.site``, which 404s on ``/onboarding/master`` —
the route belongs to the Mini App SPA. Whoever read either copy set the
wrong value and got the same dead link the guard exists to prevent. One
string, imported twice, is what keeps the correction from decaying.

Registered from :class:`apps.admin_api.apps.AdminApiConfig` — the app
that owns the endpoint embedding the value.
"""

from __future__ import annotations

from typing import Any

from django.core.checks import Warning as CheckWarning


def check_site_domain(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """Warn when the master-invite web fallback would point at localhost."""

    from django.conf import settings

    from apps.admin_api.views_invite import (
        SITE_DOMAIN_HINT,
        _site_domain,
        _site_domain_is_loopback,
    )

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
                f"{SITE_DOMAIN_HINT} Until then the web fallback is "
                "suppressed: the owner's screen has no address to copy, "
                "and the invite DM has only its open_app button."
            ),
            id="admin_api.W001",
        )
    ]


def check_bot_web_app(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """Warn when no ``open_app`` button can be built for a master invite.

    DRF-1349 — the same shape of defect as W001 above, one layer in.
    W001 guards the *fallback*; this one guards the only entry that
    actually works.

    A MAX Mini App is entered from an ``open_app`` button on the message,
    and that button needs the bot's Mini App name. Without
    ``MAX_BOT_WEB_APP`` the invite DM degrades to a bare address — which
    on a phone opens the external browser, where MAX hands the Mini App
    no ``initData`` and the onboarding cannot start — or, with no usable
    ``SITE_DOMAIN`` either, is not sent at all.

    Nobody on our side observes any of that. The endpoint answers 201
    either way; the only witness is the invited master, who has no
    channel to report «nothing opens» to anyone who could act. That is
    why this is a deploy-time check and not a runtime log line alone.
    """

    from apps.admin_api.views_invite import _sender_web_app

    # Resolved exactly as the dispatch resolves it, so the check and the
    # runtime cannot disagree. At check time there is no ``bot_scope``,
    # which is also true of the invite endpoint — both land on the global.
    if _sender_web_app():
        return []
    return [
        CheckWarning(
            "MAX_BOT_WEB_APP is not set — master invites cannot carry an "
            "open_app button, which is the only way to open a MAX Mini App.",
            hint=(
                "Set MAX_BOT_WEB_APP to the bot's Mini App name (the same "
                "value the welcome keyboard uses). Without it the invite DM "
                "falls back to a web address that opens the external "
                "browser, where MAX passes no initData and the Mini App "
                "answers «MAX не передал данные для входа»; with SITE_DOMAIN "
                "also unset the DM is not sent at all and the dispatch "
                "reports delivery=failed."
            ),
            id="admin_api.W002",
        )
    ]
