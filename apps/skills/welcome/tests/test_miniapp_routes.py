"""Every Mini App path the welcome skill builds must be a real route (DRF-1326).

## Why this file exists

DRF-1326: the welcome keyboard built Mini App addresses in two forms at
once — half the calls passed a bare slug (``profile``, ``catalog``,
``visits``), half a full client path (``customer/wellness``). No value
of ``MAX_MINIAPP_URL`` could satisfy both, and ``visits`` was not a
route in any form: the client screen is ``/customer/records``. The
defect stayed invisible only because both Mini App settings are empty on
the pilot, which drops the buttons entirely — filling the setting would
have shipped live buttons into the SPA catch-all.

## Why parse the sources instead of restating them

``https://proapp.gobeauty.site`` answers **200 on any path** — it is an
SPA fallback, so an HTTP probe proves nothing about a route existing. A
literal list of routes copied into this file would prove nothing either:
it would drift from ``App.tsx`` the first time someone renames a screen,
and drift *silently*, since a stale expectation still matches a stale
button. So the route table is read out of ``apps/miniapp/src/App.tsx``
itself, and the slug resolution out of ``apps/miniapp/src/lib/max-sdk.ts``
itself. Rename a route in the source and this file follows; point a
button at a route that is not there and it fails.

The parsers are deliberately strict — :func:`_miniapp_routes` and
:func:`_route_map` raise when they find nothing recognisable rather than
returning an empty collection, and :class:`TestParsersSeeSomething` pins
the shapes they depend on. Without that, a parser that stopped matching
would turn the file green instead of red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.skills.welcome.skill import (
    MINIAPP_ROUTES,
    _s5_first_action_buttons,
    _welcome_buttons,
)

#: Repo root — this file is ``<root>/apps/skills/welcome/tests/``.
_ROOT = Path(__file__).resolve().parents[4]
_APP_TSX = _ROOT / "apps" / "miniapp" / "src" / "App.tsx"
_MAX_SDK_TS = _ROOT / "apps" / "miniapp" / "src" / "lib" / "max-sdk.ts"

#: A Mini App base that is a bare domain, per the DRF-1326 contract.
_BASE = "https://miniapp-dev.example"


# ---------------------------------------------------------------------------
# Source-of-truth readers
# ---------------------------------------------------------------------------


def _miniapp_routes() -> frozenset[str]:
    """Every ``<Route path="…">`` declared in ``App.tsx``.

    The catch-all ``path="*"`` is excluded on purpose. It is what makes a
    wrong address *look* fine in a browser — React Router renders
    ``HelloScreen`` for anything unmatched, exactly as the SPA fallback
    answers 200 for anything. Counting it as a route would make this
    whole file assert nothing.
    """
    src = _APP_TSX.read_text(encoding="utf-8")
    routes = frozenset(
        path for path in re.findall(r'path="([^"]+)"', src) if path != "*" and path.startswith("/")
    )
    if not routes:
        raise AssertionError(
            f"No <Route path=...> found in {_APP_TSX} — the parser and the "
            "source have drifted apart. Fix the parser; do not delete the test."
        )
    return routes


def _route_map() -> dict[str, str]:
    """``_ROUTE_MAP`` from ``max-sdk.ts`` — the Mini App's slug→path table.

    This is the consumer side of the ``open_app`` payload: MAX forwards
    the slug in ``initData.start_param`` and the Mini App resolves it
    here. A slug that resolves to a non-route is just as dead as a bad
    link, and is *harder* to see, because the address never appears in
    the button.
    """
    src = _MAX_SDK_TS.read_text(encoding="utf-8")
    body = re.search(
        r"const _ROUTE_MAP: Record<string, string> = \{(.*?)\n\};",
        src,
        re.DOTALL,
    )
    if body is None:
        raise AssertionError(
            f"_ROUTE_MAP not found in {_MAX_SDK_TS} — the parser and the "
            "source have drifted apart. Fix the parser; do not delete the test."
        )
    pairs = dict(re.findall(r'^\s*(\w+):\s*"([^"]+)",', body.group(1), re.MULTILINE))
    if not pairs:
        raise AssertionError(f"_ROUTE_MAP in {_MAX_SDK_TS} parsed as empty.")
    return pairs


def _route_exists(path: str, routes: frozenset[str]) -> bool:
    """Does ``path`` match a declared route, params included?

    Segment-wise, so ``/customer/records/42`` matches the declared
    ``/customer/records/:bookingId``. No welcome button carries a param
    today; the matcher handles them anyway so that the first one which
    does gets a truthful answer instead of a false failure.
    """
    wanted = path.rstrip("/").split("/")
    for route in routes:
        declared = route.rstrip("/").split("/")
        if len(declared) != len(wanted):
            continue
        if all(d.startswith(":") or d == w for d, w in zip(declared, wanted)):
            return True
    return False


def _path_of(url: str) -> str:
    """The in-app path of a link button built on :data:`_BASE`."""
    assert url.startswith(_BASE + "/"), f"button URL is not built on the base: {url!r}"
    return url[len(_BASE) :]


def _built_buttons(settings, *, web_app: str, miniapp_url: str, pilot_ux: bool):
    """Every button both welcome surfaces build under one configuration."""
    settings.MAX_BOT_WEB_APP = web_app
    settings.MAX_MINIAPP_URL = miniapp_url
    settings.PILOT_CONVERSATIONAL_UX = pilot_ux
    return _welcome_buttons() + _s5_first_action_buttons()


#: Both rollback-flag states. OFF restores the pre-DRF-963 keyboard, and
#: that branch built ``visits`` too — a bug hiding behind a flag is still
#: shipped code, so it is checked under the same rules.
_UX_FLAGS = [pytest.param(True, id="pilot-ux-on"), pytest.param(False, id="pilot-ux-off")]


# ---------------------------------------------------------------------------
# The parsers must actually see the sources
# ---------------------------------------------------------------------------


class TestParsersSeeSomething:
    """Guards against this file passing by reading nothing.

    Every assertion further down has the shape "the thing I built is in
    the set I parsed". An empty parse would fail loudly, but an empty
    *button* list would pass vacuously — so both ends are pinned here.
    """

    def test_app_tsx_route_table_is_read(self):
        routes = _miniapp_routes()
        assert "/customer/records" in routes
        assert "/customer/food-scanner/capture" in routes
        assert len(routes) > 20, f"suspiciously few routes parsed: {sorted(routes)}"

    def test_catch_all_is_not_treated_as_a_route(self):
        assert not _route_exists("/no/such/screen/at/all", _miniapp_routes())

    def test_route_map_is_read(self):
        assert _route_map()["open_home"] == "/customer/main"

    def test_link_config_actually_builds_link_buttons(self, settings):
        buttons = _built_buttons(settings, web_app="", miniapp_url=_BASE, pilot_ux=True)
        assert [b for b in buttons if "url" in b], "no link buttons built to check"

    def test_web_app_config_actually_builds_open_app_buttons(self, settings):
        buttons = _built_buttons(settings, web_app="id583_bot", miniapp_url="", pilot_ux=True)
        assert [b for b in buttons if b.get("web_app")], "no open_app buttons built"


# ---------------------------------------------------------------------------
# The route table itself
# ---------------------------------------------------------------------------


class TestRouteTable:
    """``MINIAPP_ROUTES`` is the producer's source of truth — pin it to both
    consumers."""

    @pytest.mark.parametrize("slug", sorted(MINIAPP_ROUTES))
    def test_every_declared_route_exists_in_app_tsx(self, slug):
        path = "/" + MINIAPP_ROUTES[slug]
        assert _route_exists(path, _miniapp_routes()), (
            f"MINIAPP_ROUTES[{slug!r}] = {path!r} is not a route in {_APP_TSX.name}. "
            "The SPA answers 200 for it anyway and renders the home screen, so "
            "only this table proves it."
        )

    @pytest.mark.parametrize("slug", sorted(MINIAPP_ROUTES))
    def test_slug_resolves_to_the_same_path_in_the_mini_app(self, slug):
        resolved = _route_map().get(slug)
        assert resolved is not None, (
            f"slug {slug!r} is emitted as an open_app payload but is missing from "
            f"_ROUTE_MAP in {_MAX_SDK_TS.name} — the Mini App would resolve it to "
            "null and land on the home screen."
        )
        assert resolved == "/" + MINIAPP_ROUTES[slug], (
            f"same button, two destinations: the link fallback opens "
            f"{'/' + MINIAPP_ROUTES[slug]!r} while the in-MAX button resolves to "
            f"{resolved!r}. That split is DRF-1326."
        )

    def test_every_declared_route_is_in_one_form(self):
        """One form, not two.

        The regression DRF-1326 describes is not any single wrong path —
        it is a table with two shapes in it, where no base setting can be
        right for both halves. Fixing the paths without fixing the shape
        leaves the trap armed for the next button.
        """
        odd = {s: p for s, p in MINIAPP_ROUTES.items() if not p.startswith("customer/")}
        assert not odd, (
            f"these paths are not in the customer/ form: {odd}. Every customer "
            "screen in App.tsx lives under /customer/; a bare slug means the base "
            "setting has to be two different things at once."
        )

    def test_no_declared_route_is_absolute(self):
        """Paths join onto the base — a leading ``/`` would be a second form."""
        odd = {s: p for s, p in MINIAPP_ROUTES.items() if p.startswith("/")}
        assert not odd, f"paths must be base-relative, got {odd}"


# ---------------------------------------------------------------------------
# What the buttons actually build
# ---------------------------------------------------------------------------


class TestBuiltButtonsReachRealScreens:
    """The class-closing check: not "the table is right" but "every button
    built from it is right", under every configuration that builds one."""

    @pytest.mark.parametrize("pilot_ux", _UX_FLAGS)
    def test_link_buttons_target_declared_routes(self, settings, pilot_ux):
        routes = _miniapp_routes()
        buttons = _built_buttons(settings, web_app="", miniapp_url=_BASE, pilot_ux=pilot_ux)
        links = [b for b in buttons if "url" in b]
        assert links
        for button in links:
            path = _path_of(button["url"])
            assert _route_exists(path, routes), (
                f"«{button['label']}» opens {path!r}, which is not a route in "
                f"{_APP_TSX.name}. The user lands on the home screen and the "
                "button reads as broken."
            )

    def test_link_buttons_tolerate_a_trailing_slash_on_the_base(self, settings):
        """``MAX_MINIAPP_URL`` gets copy-pasted; a trailing slash must not
        produce ``//customer/…``, which is a different path."""
        routes = _miniapp_routes()
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = _BASE + "/"
        settings.PILOT_CONVERSATIONAL_UX = True
        for button in _welcome_buttons() + _s5_first_action_buttons():
            if "url" not in button:
                continue
            assert _route_exists(_path_of(button["url"]), routes), button

    @pytest.mark.parametrize("pilot_ux", _UX_FLAGS)
    def test_open_app_payloads_resolve_to_declared_routes(self, settings, pilot_ux):
        routes = _miniapp_routes()
        route_map = _route_map()
        buttons = _built_buttons(settings, web_app="id583_bot", miniapp_url="", pilot_ux=pilot_ux)
        open_app = [b for b in buttons if b.get("web_app")]
        assert open_app
        for button in open_app:
            slug = button["callback"]
            assert slug in MINIAPP_ROUTES, (
                f"«{button['label']}» sends payload {slug!r}, which is not in "
                "MINIAPP_ROUTES — the producer no longer knows where it goes."
            )
            resolved = route_map.get(slug)
            assert resolved is not None, f"{slug!r} missing from _ROUTE_MAP"
            assert _route_exists(resolved, routes), (
                f"«{button['label']}» resolves to {resolved!r}, not a route in {_APP_TSX.name}."
            )

    def test_my_visits_opens_the_records_screen(self, settings):
        """The named casualty of DRF-1326.

        ``visits`` was not a near-miss for a real route — no route by
        that name has ever existed. Pinned by name so a future rename of
        the screen has to come here and decide, rather than quietly
        restoring a dead button.
        """
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = _BASE
        settings.PILOT_CONVERSATIONAL_UX = False  # the branch that builds it
        visits = [b for b in _welcome_buttons() if b.get("label") == "📋 Мои визиты"]
        assert visits, "the rollback keyboard no longer offers «Мои визиты»"
        assert visits[0]["url"] == f"{_BASE}/customer/records"


# ---------------------------------------------------------------------------
# The fallback ladder
# ---------------------------------------------------------------------------


class TestZeroConfigLadder:
    """With neither setting filled, a Mini App button must be **absent**.

    Not present-but-inert: a button that answers nothing is worse than no
    button, because the user reads silence as the bot being broken. This
    is also what has been hiding DRF-1326 on the pilot — both settings
    are empty, so the broken buttons were never built.
    """

    @pytest.mark.parametrize("pilot_ux", _UX_FLAGS)
    def test_no_miniapp_button_without_config(self, settings, pilot_ux):
        buttons = _built_buttons(settings, web_app="", miniapp_url="", pilot_ux=pilot_ux)
        for button in buttons:
            assert "url" not in button, f"link button built with no base: {button}"
            assert "web_app" not in button, f"open_app button built with no app: {button}"
            assert button["callback"] not in MINIAPP_ROUTES, (
                f"«{button['label']}» degraded into a bare callback "
                f"{button['callback']!r}. Nothing in the bot answers a Mini App "
                "slug, so the tap does nothing at all."
            )

    @pytest.mark.parametrize("pilot_ux", _UX_FLAGS)
    def test_surviving_buttons_are_answerable_callbacks(self, settings, pilot_ux):
        """Whatever does survive zero-config must be a bot-native callback."""
        buttons = _built_buttons(settings, web_app="", miniapp_url="", pilot_ux=pilot_ux)
        for button in buttons:
            assert button.get("callback"), f"button with no action at all: {button}"
