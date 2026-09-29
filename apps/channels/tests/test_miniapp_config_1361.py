"""The client bot's Mini App comes from its registry entry, not the global settings (DRF-1361).

Owner decision 24.08: «fill ``MAX_MINIAPP_URL``» is NO-GO — the registry keeps
``web_app`` / ``miniapp_url`` per bot and a declared entry reads only its own
prefix, but the client welcome read the globals, so the client bot's value
was invisible to its own buttons. One accessor now answers for every button
builder (:mod:`apps.channels.miniapp_config`); the salon path (``bot_scope``)
is untouched, and — the radius correction of 19.09 — the client path does
NOT enter ``bot_scope``: ``outbound._token`` keeps reading
``settings.MAX_BOT_TOKEN`` there, and a node pins it.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from apps.channels.bot_context import bot_scope, current_bot
from apps.channels.bot_registry import BotEntry
from apps.channels.max import outbound
from apps.channels.miniapp_config import GLOBAL_STREAM, miniapp_target

REPO_ROOT = Path(__file__).resolve().parents[3]

CLIENT = BotEntry(
    slug="client",
    webhook_secret="wh-client",  # noqa: S106  # pragma: allowlist secret
    api_token="tok-client",  # noqa: S106  # pragma: allowlist secret
    stream=GLOBAL_STREAM,
    web_app="client_mini_app",
    miniapp_url="https://client.example",
)
SALON = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # noqa: S106  # pragma: allowlist secret
    api_token="tok-salon",  # noqa: S106  # pragma: allowlist secret
    stream="max_salon",
    tenant_slug="pilot",
    web_app="salon_mini_app",
    miniapp_url="https://salon.example",
)
FOREIGN = "foreign-mini-app-from-settings"


@pytest.fixture(autouse=True)
def _foreign_settings(settings):
    """The globals say something deliberately wrong: a reader that consults them shows it."""

    settings.MAX_BOT_WEB_APP = FOREIGN
    settings.MAX_MINIAPP_URL = "https://foreign.example"
    settings.MAX_BOT_TOKEN = "tok-global"  # noqa: S105  # pragma: allowlist secret
    settings.MAX_BOT_REGISTRY = (CLIENT, SALON)
    assert current_bot() is None


class TestTheAccessor:
    def test_the_bot_in_scope_wins(self) -> None:
        with bot_scope(SALON):
            target = miniapp_target()
        assert (target.web_app, target.miniapp_url, target.source) == (
            "salon_mini_app",
            "https://salon.example",
            "scope",
        )

    def test_without_a_scope_the_max_global_entry_answers_for_the_client_path(self) -> None:
        target = miniapp_target()
        assert (target.web_app, target.miniapp_url) == ("client_mini_app", "https://client.example")
        assert target.source == "registry:max_global"
        assert FOREIGN not in (target.web_app, target.miniapp_url)

    def test_without_a_max_global_entry_the_globals_are_the_fallback(self, settings) -> None:
        """Single-bot mode and the existing test body (bot_registry.py:279)."""

        settings.MAX_BOT_REGISTRY = (SALON,)
        target = miniapp_target()
        assert (target.web_app, target.miniapp_url, target.source) == (
            FOREIGN,
            "https://foreign.example",
            "settings",
        )

    def test_an_entry_with_an_empty_web_app_is_no_button_not_the_global_one(self, settings) -> None:
        """A declared entry reads only its own prefix — the global is not its fallback."""

        settings.MAX_BOT_REGISTRY = (dataclasses.replace(CLIENT, web_app="", miniapp_url=""), SALON)
        target = miniapp_target()
        assert (target.web_app, target.miniapp_url) == ("", "")

    def test_the_outbound_token_on_the_client_path_is_untouched(self) -> None:
        """Radius: the accessor never enters bot_scope, so sending is unchanged."""

        before = outbound._token()
        miniapp_target()
        assert before == outbound._token() == "tok-global"
        assert current_bot() is None


class TestTheClientButtonsReadTheEntry:
    def test_the_welcome_keyboard_opens_the_client_entry_not_the_setting(self, settings) -> None:
        from apps.skills.welcome import skill as welcome

        settings.PILOT_UX_ENABLED = True
        buttons = welcome._welcome_buttons() + welcome._s5_first_action_buttons()
        open_app = [b for b in buttons if b.get("web_app")]
        assert open_app, buttons  # positive: the keyboard carries open_app buttons at all
        assert {b["web_app"] for b in open_app} == {"client_mini_app"}
        assert not [b for b in buttons if b.get("url")]

    def test_the_marketplace_ladder_reads_the_entry(self) -> None:
        from apps.skills.menu import marketplace

        assert marketplace._config() == ("client_mini_app", "https://client.example")
        data = marketplace.health_request_action_data()
        (button, *_) = data["buttons"]
        assert button.get("web_app") == "client_mini_app"

    def test_the_visit_buttons_read_the_entry(self) -> None:
        from apps.orchestrator import visits

        history = visits.history_app_button()
        assert history is not None and history.get("web_app") == "client_mini_app"
        reschedule = visits.reschedule_button(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", label="Перенести"
        )
        assert reschedule is not None and reschedule.get("web_app") == "client_mini_app"

    def test_under_the_salon_scope_the_same_builders_open_the_salon_entry(self) -> None:
        from apps.skills.menu import marketplace

        with bot_scope(SALON):
            assert marketplace._config() == ("salon_mini_app", "https://salon.example")


# --- census by role: nobody else reads the globals ------------------------------

#: Modules allowed to read the two settings by name: the accessor itself, the
#: legacy synthesis of the registry, and the admin_api pair that documents its
#: own prefix-then-global contract (invite link, DRF-1349; check W002, DRF-2021).
_ALLOWED_READERS = {
    "apps/channels/miniapp_config.py",
    "apps/channels/bot_registry.py",
    "apps/admin_api/views_invite.py",
    "apps/admin_api/checks.py",
}
_SETTING_NAMES = {"MAX_BOT_WEB_APP", "MAX_MINIAPP_URL"}


def _reads_setting(node: ast.AST) -> str | None:
    """``settings.X`` or ``getattr(settings, "X", …)`` → X when X is one of ours."""

    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "settings" and node.attr in _SETTING_NAMES:
            return node.attr
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        if (
            len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "settings"
        ):
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and arg.value in _SETTING_NAMES:
                return str(arg.value)
    return None


def census(source: str, *, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    return [
        f"{filename}:{getattr(node, 'lineno', 0)}: {name}"
        for node in ast.walk(tree)
        if (name := _reads_setting(node)) is not None
    ]


class TestCensus:
    def test_no_button_builder_reads_the_global_mini_app_settings(self) -> None:
        hits: list[str] = []
        seen = 0
        for path in sorted((REPO_ROOT / "apps").rglob("*.py")):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            found = census(path.read_text(encoding="utf-8"), filename=rel)
            seen += len(found)
            if rel not in _ALLOWED_READERS:
                hits.extend(found)
        assert seen >= 4, (
            "the census found almost no readers — the allowed ones alone read the pair"
        )
        assert hits == [], (
            "a builder reads the global Mini App settings instead of miniapp_target(): "
            + "; ".join(hits)
        )

    def test_the_census_sees_both_spellings(self) -> None:
        planted = (
            "from django.conf import settings\n"
            "a = settings.MAX_BOT_WEB_APP\n"
            'b = getattr(settings, "MAX_MINIAPP_URL", "")\n'
            'c = getattr(settings, "MAX_BOT_TOKEN", "")\n'
        )
        assert [h.split(": ")[1] for h in census(planted, filename="<planted>")] == [
            "MAX_BOT_WEB_APP",
            "MAX_MINIAPP_URL",
        ]
