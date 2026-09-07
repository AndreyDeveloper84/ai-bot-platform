"""Deploy-time guards for the master-invite entry (DRF-1079, DRF-1349).

### What went wrong without it

``SITE_DOMAIN`` has a repository default of ``http://localhost:5173``
(``config/settings/base.py:537``) and is named in no env template. On
the pilot it was never set, so every master invite carried a web
fallback to a port on a machine the invited master does not own —
verified in the running container, the variable is absent entirely.

Nothing complained. The endpoint answered 201 and the owner's screen
showed a web address. The only observer of the defect was the master,
who has no way to report «this link opens nothing» to anyone who could
act on it.

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
                "suppressed: the owner's screen has no address to copy."
            ),
            id="admin_api.W001",
        )
    ]


def check_bot_web_app(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """Warn when no ``open_app`` button can be built at all.

    DRF-1349 — the same shape of defect as W001 above, one layer in.
    W001 guards the *fallback*; this one guards the only entry that
    actually works.

    A MAX Mini App is entered from an ``open_app`` button on a message,
    and that button needs the bot's Mini App name. Without
    ``MAX_BOT_WEB_APP`` every button the bot builds degrades to a bare
    address — which on a phone opens the external browser, where MAX
    hands the Mini App no ``initData`` and nothing can start.

    ### Why the check outlived the invite DM it was written for

    §44.4 removed the master-invite DM, which is what this check
    originally guarded. It is NOT dead with it: the same setting is what
    the welcome keyboard (``apps.skills.welcome.skill``), the
    marketplace menu (``apps.skills.menu.marketplace``), the visit
    prompts (``apps.orchestrator.visits``) and the bot registry
    (``apps.channels.bot_registry``) each read to decide whether they can
    offer an ``open_app`` button. It is the only deploy-time guard any of
    them have.

    Nobody on our side observes the failure. Every one of those paths
    answers successfully either way; the only witness is the person
    holding the phone, who has no channel to report «nothing opens» to
    anyone who could act. That is why this is a deploy-time check and
    not a runtime log line alone.
    """

    from apps.admin_api.views_invite import _sender_web_app

    # Resolved through the helper rather than read off settings: the
    # surrounding ``bot_scope`` wins over the legacy global, and a check
    # that read the global alone would warn about a contour configured
    # per bot. At check time there is no scope, so this lands on the
    # global — which is the answer for an unscoped runtime too.
    if _sender_web_app():
        return []
    return [
        CheckWarning(
            "MAX_BOT_WEB_APP is not set — the bot cannot carry an open_app "
            "button, which is the only way to open a MAX Mini App.",
            hint=(
                "Set MAX_BOT_WEB_APP to the bot's Mini App name (the same "
                "value the welcome keyboard uses). Without it every "
                "open_app button the bot would offer falls back to a web "
                "address that opens the external browser, where MAX passes "
                "no initData and the Mini App answers «MAX не передал "
                "данные для входа»."
            ),
            id="admin_api.W002",
        )
    ]
