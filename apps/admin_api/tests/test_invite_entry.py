"""The invite DM must carry a working entry into the Mini App (DRF-1349).

## Why this file exists

Found 30.08 by the owner on the **first live invitation of the pilot** —
not by a test, not by a review, but by walking the flow by hand. The DM
built in :func:`apps.admin_api.views_invite._dispatch_max_dm` offered two
addresses and no button:

1. ``max://bot/<slug>?start=master_invite_<uuid>`` — MAX does not
   implement that scheme. The phone answered «Не удалось открыть
   ссылку. Установите браузер на устройстве».
2. ``https://<miniapp>/onboarding/master?token=<uuid>`` — opens in the
   **browser**, and MAX hands a browser no ``initData``. The Mini App
   answers «MAX не передал данные для входа»
   (``apps/miniapp/src/screens/HelloScreen.tsx``), and the backend agrees:
   ``validate_invite_token`` filters the token by the tenant of the
   session's ``BotUser`` (``apps/master_api/views.py``), so with no
   session there is no tenant and no way to check the token at all.

The working entry — the one the welcome grid already uses — is an
``open_app`` button carried as an ``inline_keyboard`` attachment on the
message itself. Nothing in the invite DM built one.

## The shape of the assertions here

Rule of this contour, re-confirmed six times over 29–30.08:

    **A negative claim needs a positive guard on the same data.**

«The message contains no ``max://``» passes on an empty message and
therefore proves nothing. So every absence check below sits next to a
presence check reading the very same dispatched call: the button exists,
its payload is the invite token, and the Mini App's own rule resolves
that payload to the onboarding route.

## Why the sources are parsed rather than restated

Same discipline as ``apps/skills/welcome/tests/test_miniapp_routes.py``
(DRF-1326), whose parsers this file imports rather than re-implements.
The slug the bot emits and the slug the Mini App resolves are written in
two languages; a literal copy of either into this file would drift
silently the first time someone renamed it, and a stale expectation
still matches stale code. So the TypeScript constants are read out of
``apps/miniapp/src/lib/max-sdk.ts`` and the route table out of
``apps/miniapp/src/App.tsx``.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.channels.max.outbound import _button_to_max
from apps.identity.models import BotUser
from apps.skills.welcome.tests.test_miniapp_routes import _miniapp_routes, _route_exists
from apps.tenancy.models import Tenant

#: Repo root — this file is ``<root>/apps/admin_api/tests/``.
_ROOT = Path(__file__).resolve().parents[3]
_APP_TSX = _ROOT / "apps" / "miniapp" / "src" / "App.tsx"
_MAX_SDK_TS = _ROOT / "apps" / "miniapp" / "src" / "lib" / "max-sdk.ts"

#: A Mini App origin that is not loopback, so ``_fallback_link`` stays on.
_SITE = "https://miniapp-dev.example"
#: A configured bot Mini App name, so the ``open_app`` branch is live.
_WEB_APP = "id583_bot"


# ---------------------------------------------------------------------------
# Source-of-truth readers (consumer side, TypeScript)
# ---------------------------------------------------------------------------


def _py_prefix() -> str:
    """The producer-side slug prefix, read at call time.

    Imported lazily and re-raised as an assertion rather than pulled in
    at module scope: a missing constant should fail each test that
    depends on it with a sentence explaining what is missing, not abort
    collection of the whole file with a bare ``ImportError``.
    """
    from apps.admin_api import views_invite

    prefix = getattr(views_invite, "MASTER_INVITE_PAYLOAD_PREFIX", None)
    if not prefix:
        raise AssertionError(
            "`MASTER_INVITE_PAYLOAD_PREFIX` not found in "
            "apps/admin_api/views_invite.py. The invite payload slug has to "
            "be a named constant on both sides of the seam — otherwise the "
            "bot and the Mini App agree only by coincidence and drift apart "
            "silently on the first rename."
        )
    return str(prefix)


def _ts_const(name: str) -> str:
    """Read a top-level ``export const <name> = "…";`` out of ``max-sdk.ts``.

    Raises rather than returning a default when the constant is absent:
    a parser that quietly finds nothing turns every assertion below into
    a comparison against emptiness, which is how a pinning test starts
    passing while the thing it pins is gone.
    """
    src = _MAX_SDK_TS.read_text(encoding="utf-8")
    found = re.search(rf'^export const {name} = "([^"]+)";', src, re.MULTILINE)
    if found is None:
        raise AssertionError(
            f"`export const {name}` not found in {_MAX_SDK_TS}. The bot's "
            "payload and the Mini App's parser are pinned to each other "
            "through this constant — fix the parser or restore the "
            "constant; do not delete the test."
        )
    return found.group(1)


def _fn_body(name: str) -> str:
    """The body of a top-level ``function <name>(…) {…}`` in ``App.tsx``.

    ``CustomerRoutes`` is the surface an *invited but not yet onboarded*
    master actually gets: ``resolve_role`` reports ``is_master`` only
    for a CatalogMaster that is ACCEPTED **and** already linked to a
    BotUser (``apps/identity/services/role_resolver.py``), and an
    invitee is neither. So the role cascade drops them into
    ``CustomerRoutes`` — and a route mounted only under
    ``masterRouteElements()`` is not there for the one person it exists
    for.
    """
    src = _APP_TSX.read_text(encoding="utf-8")
    found = re.search(rf"\nfunction {name}\([^)]*\)[^{{]*\{{(.*?)\n\}}", src, re.DOTALL)
    if found is None:
        raise AssertionError(
            f"function {name}(…) not found in {_APP_TSX} — the parser and the "
            "source have drifted apart. Fix the parser; do not delete the test."
        )
    return found.group(1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _invite_url() -> str:
    return reverse("admin_api:master_invite_create")


def _valid_body() -> dict[str, Any]:
    return {
        "name": "Анна Петрова",
        "contact_method": "max_username",
        "contact_value": "@anna_styl",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
    }


@pytest.fixture
def sent(client: Client, owner_bot_user: BotUser, tenant: Tenant, settings):
    """Dispatch one real invite and return the ``send_message`` kwargs."""
    settings.SITE_DOMAIN = _SITE
    settings.MAX_BOT_WEB_APP = _WEB_APP
    with patch("apps.admin_api.views_invite.max_outbound.send_message") as mock:
        mock.return_value = {"ok": True}
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
    assert resp.status_code == 201, resp.content
    mock.assert_called_once()
    return {"kwargs": mock.call_args.kwargs, "token": resp.json()["invite_token"]}


def _open_app_buttons(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """Every ``open_app`` button in the dispatched message's keyboard."""
    out: list[dict[str, Any]] = []
    for att in kwargs.get("attachments") or []:
        if att.get("type") != "inline_keyboard":
            continue
        for row in att.get("payload", {}).get("buttons", []):
            out.extend(b for b in row if b.get("type") == "open_app")
    return out


# ---------------------------------------------------------------------------
# The parsers must actually see the sources
# ---------------------------------------------------------------------------


class TestParsersSeeSomething:
    """Guards against this file passing by reading nothing."""

    def test_app_tsx_route_table_is_read(self):
        assert "/customer/records" in _miniapp_routes()

    def test_customer_routes_body_is_read(self):
        body = _fn_body("CustomerRoutes")
        assert 'path="/customer/catalog"' in body, (
            "CustomerRoutes parsed but does not contain a route it is known "
            "to declare — the regex is matching the wrong span."
        )

    def test_admin_routes_body_is_read(self):
        assert 'path="*"' in _fn_body("AdminRoutes"), (
            "AdminRoutes parsed but does not contain the catch-all it is "
            "known to declare — the regex is matching the wrong span."
        )


# ---------------------------------------------------------------------------
# The seam: one slug, two languages
# ---------------------------------------------------------------------------


class TestSlugIsPinnedOnBothSides:
    """The slug the bot emits must be the slug the Mini App parses.

    Not «both look plausible» — literally the same string, read out of
    the two sources that have to agree, plus a route that actually
    exists behind it.
    """

    def test_prefix_matches_between_bot_and_mini_app(self):
        assert _py_prefix() == _ts_const("MASTER_INVITE_PAYLOAD_PREFIX"), (
            "the bot builds one prefix and the Mini App looks for another — "
            "the invite button would resolve to null and land the invited "
            "master on the home screen with no error at all."
        )

    def test_onboarding_path_is_a_real_route(self):
        path = _ts_const("MASTER_ONBOARDING_PATH")
        assert _route_exists(path, _miniapp_routes()), (
            f"{path!r} is not a <Route path=…> in {_APP_TSX.name}. The SPA "
            "answers 200 for any path and renders the home screen, so only "
            "this check proves the destination is real."
        )

    def test_payload_survives_the_max_flat_slug_guard(self):
        """UUID dashes must pass MAX's ``open_app`` payload restriction.

        Guard 3 in ``apps/channels/max/outbound.py`` rejects ``=``, ``&``
        and ``?`` because a querystring-shaped payload gets HTTP 400 from
        MAX and poisons the consumer PEL. A UUID contains none of those —
        pinned here so nobody "fixes" the slug into a querystring.
        """
        payload = f"{_py_prefix()}{uuid.uuid4()}"
        wire = _button_to_max(
            {"label": "Принять приглашение", "callback": payload, "web_app": _WEB_APP}
        )
        assert wire["type"] == "open_app"
        assert wire["payload"] == payload


# ---------------------------------------------------------------------------
# What the invite DM actually sends
# ---------------------------------------------------------------------------


class TestInviteDmCarriesTheWorkingEntry:
    """Presence first, absence second — on the same dispatched message."""

    def test_dm_carries_an_open_app_button(self, sent):
        buttons = _open_app_buttons(sent["kwargs"])
        assert buttons, (
            "the invite DM carries no `open_app` button. A MAX Mini App can "
            "only be entered from a button on the message itself; a bare "
            "address in the text cannot open it (max:// is not implemented "
            "by MAX, https:// opens a browser that gets no initData)."
        )

    def test_the_button_payload_is_this_invite_token(self, sent):
        buttons = _open_app_buttons(sent["kwargs"])
        assert buttons
        payload = buttons[0]["payload"]
        assert payload == f"{_py_prefix()}{sent['token']}", (
            f"button payload {payload!r} does not carry the invite token "
            f"{sent['token']!r} — the Mini App would open with nothing to claim."
        )

    def test_the_button_payload_resolves_in_the_mini_app(self, sent):
        """The producer's string, run through the consumer's own rule.

        The Mini App resolves the payload with a strict prefix + UUID
        match (``parseStartRoute`` in ``max-sdk.ts``). This mirrors that
        rule against the source constants rather than restating a
        literal, so a rename on either side fails here.
        """
        buttons = _open_app_buttons(sent["kwargs"])
        assert buttons
        payload = buttons[0]["payload"]
        prefix = _ts_const("MASTER_INVITE_PAYLOAD_PREFIX")
        assert payload.startswith(prefix)
        rest = payload[len(prefix) :]
        # Must be a well-formed UUID — the Mini App refuses anything else,
        # so a payload it would reject must fail here too.
        uuid.UUID(rest)
        assert _route_exists(_ts_const("MASTER_ONBOARDING_PATH"), _miniapp_routes())

    def test_the_button_targets_the_configured_mini_app(self, sent):
        buttons = _open_app_buttons(sent["kwargs"])
        assert buttons
        assert buttons[0].get("web_app") == _WEB_APP

    def test_no_max_scheme_address_in_the_text(self, sent):
        """Absence check — paired with the presence checks above.

        On its own this passes for an empty message. It is meaningful
        only because the same dispatched call is asserted to carry a
        button with a resolvable payload.
        """
        assert "max://" not in sent["kwargs"]["text"], (
            "the DM still offers a max:// address. MAX does not implement "
            "the scheme — the device answers «Не удалось открыть ссылку»."
        )

    def test_no_promise_of_a_web_version(self, sent):
        """«Не открывается в MAX? Используйте веб-версию» must be gone.

        It pointed the reader at the one path that cannot work, and the
        owner followed it on 30.08. The Mini App needs ``initData``,
        which a browser never receives.
        """
        assert "веб-версию" not in sent["kwargs"]["text"], (
            "the DM still directs the invited master to a web version. "
            "Opening the invite outside MAX cannot work: no initData means "
            "no session, and `validate_invite_token` resolves the token "
            "through the session's tenant."
        )


# ---------------------------------------------------------------------------
# The deploy-time guard
# ---------------------------------------------------------------------------


class TestBotWebAppSystemCheck:
    """``manage.py check`` is where an unset Mini App name has to surface.

    The runtime ERROR line lands in a log nobody reads until someone
    already complained; the endpoint answers 201 either way. The one
    observer of a buttonless invite is the invited master, who has no
    channel to report «nothing opens» to anyone who could act — which is
    exactly how this survived to the first live invitation.
    """

    def test_flags_the_unset_mini_app_name(self, settings):
        from apps.admin_api.checks import check_bot_web_app

        settings.MAX_BOT_WEB_APP = ""
        assert [w.id for w in check_bot_web_app(None)] == ["admin_api.W002"]

    def test_silent_once_configured(self, settings):
        """The positive half — a guard that always fires is noise.

        Without this, the check above would also pass on an
        implementation that warns unconditionally, and the first
        operator to see W002 on a correctly configured contour would
        learn to ignore it.
        """
        from apps.admin_api.checks import check_bot_web_app

        settings.MAX_BOT_WEB_APP = _WEB_APP
        assert check_bot_web_app(None) == []

    def test_the_hint_names_the_variable_to_set(self, settings):
        from apps.admin_api.checks import check_bot_web_app

        settings.MAX_BOT_WEB_APP = ""
        (warning,) = check_bot_web_app(None)
        assert warning.hint is not None
        assert "MAX_BOT_WEB_APP" in warning.hint


# ---------------------------------------------------------------------------
# The fourth break — the destination has to be mounted for the invitee
# ---------------------------------------------------------------------------


class TestOnboardingRouteIsReachableBeforeTheMasterRole:
    """A perfect button into an unmounted route is still a dead end.

    ``resolve_role`` reports ``is_master`` only for a CatalogMaster row
    that is ACCEPTED and linked to the calling BotUser. An invitee is
    PENDING and unlinked by definition — acceptance is what the
    onboarding screen exists to perform. So the invitee boots into the
    customer surface, and if ``/onboarding/master`` lives only inside
    ``masterRouteElements()`` the catch-all renders ``HelloScreen``
    instead: the screen is reachable only by someone who no longer
    needs it.
    """

    #: The shared element every surface mounts. Named once here so the
    #: two checks below cannot drift into testing different things.
    _SHARED = "inviteOnboardingRouteElements"

    def test_the_shared_element_declares_the_onboarding_route(self):
        """The indirection must actually contain the route.

        Without this, the two surface checks below would be satisfied by
        an element that mounts nothing — «the function is called» is not
        «the route exists».
        """
        path = _ts_const("MASTER_ONBOARDING_PATH")
        assert f'path="{path}"' in _fn_body(self._SHARED), (
            f"{self._SHARED}() does not declare {path}."
        )

    @pytest.mark.parametrize("surface", ["CustomerRoutes", "AdminRoutes"])
    def test_pre_master_surfaces_mount_the_onboarding_route(self, surface):
        """Both surfaces an invitee can boot into must mount it.

        ``CustomerRoutes`` is where a plain invited master lands;
        ``AdminRoutes`` is where an owner who adds herself as a master
        lands, since she is not a master until she accepts either. The
        master and unified surfaces get it through
        ``masterRouteElements()`` and are covered by the same element.
        """
        body = _fn_body(surface)
        mounted = f"{self._SHARED}()" in body or (
            f'path="{_ts_const("MASTER_ONBOARDING_PATH")}"' in body
        )
        assert mounted, (
            f"/onboarding/master is not mounted in {surface}. An invited "
            "master is PENDING and unlinked, so /api/v1/me returns "
            "is_master=false and the role cascade lands them here — where the "
            "catch-all swallows the invite and it dies silently."
        )
