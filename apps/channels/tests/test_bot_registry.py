"""Bot registry parsing and resolution tests (DRF-1061).

Pure-function tests — no Django, no DB, no network. The registry is parsed
once at settings load, so a bug here is a boot-time bug affecting every bot
on the deployment; these tests are the cheapest place to catch it.

The two properties that carry the most weight:

* **legacy fallback** — a deployment that never declares ``MAX_BOTS`` must
  behave exactly as it did before DRF-1061. The whole change is additive on
  the strength of this.
* **ambiguity is rejected, never resolved** — a duplicate secret or slug
  must fail loudly at boot. Silently picking one entry would misroute every
  update for the other bot, and it would look stable while doing it.
"""

from __future__ import annotations

import pytest

from apps.channels.bot_registry import (
    LEGACY_SLUG,
    BotEntry,
    BotRegistryConfigurationError,
    api_tokens,
    parse_registry,
    resolve_by_slug,
    resolve_by_webhook_secret,
    with_legacy_fallback,
)

# Fixture credentials. Named constants rather than inline literals so the
# secret scanner has one place to allowlist instead of one per assertion.
SECRET = "ws"  # pragma: allowlist secret
TOKEN = "tok"  # pragma: allowlist secret

TWO_BOTS = {
    "MAX_BOTS": "client,salon",
    "MAX_BOT_CLIENT_WEBHOOK_SECRET": "secret-client",  # pragma: allowlist secret
    "MAX_BOT_CLIENT_API_TOKEN": "token-client",  # pragma: allowlist secret
    "MAX_BOT_CLIENT_STREAM": "max_global",
    "MAX_BOT_SALON_WEBHOOK_SECRET": "secret-salon",  # pragma: allowlist secret
    "MAX_BOT_SALON_API_TOKEN": "token-salon",  # pragma: allowlist secret
    "MAX_BOT_SALON_TENANT_SLUG": "formula-tela",
    "MAX_BOT_SALON_STREAM": "max_salon",
    "MAX_BOT_SALON_MINIAPP_URL": "https://miniapp-dev.example/",
}


class TestParsing:
    def test_parses_two_bots_in_declaration_order(self):
        entries = parse_registry(TWO_BOTS)

        assert [e.slug for e in entries] == ["client", "salon"]
        client, salon = entries
        assert client.stream == "max_global"
        assert client.tenant_slug == ""
        assert client.is_tenant_less is True
        assert salon.stream == "max_salon"
        assert salon.tenant_slug == "formula-tela"
        assert salon.is_tenant_less is False
        assert salon.miniapp_url == "https://miniapp-dev.example/"

    def test_empty_or_missing_max_bots_yields_no_entries(self):
        assert parse_registry({}) == ()
        assert parse_registry({"MAX_BOTS": ""}) == ()
        assert parse_registry({"MAX_BOTS": "   "}) == ()

    def test_stream_defaults_to_max(self):
        entries = parse_registry(
            {
                "MAX_BOTS": "solo",
                "MAX_BOT_SOLO_WEBHOOK_SECRET": "s",  # pragma: allowlist secret
                "MAX_BOT_SOLO_API_TOKEN": "t",  # pragma: allowlist secret
            }
        )
        assert entries[0].stream == "max"

    def test_values_are_trimmed(self):
        entries = parse_registry(
            {
                "MAX_BOTS": " solo ",
                "MAX_BOT_SOLO_WEBHOOK_SECRET": "  s  ",  # pragma: allowlist secret
                "MAX_BOT_SOLO_API_TOKEN": "\tt\n",  # pragma: allowlist secret
                "MAX_BOT_SOLO_TENANT_SLUG": " formula-tela ",
            }
        )
        assert entries[0].webhook_secret == "s"
        assert entries[0].api_token == "t"
        assert entries[0].tenant_slug == "formula-tela"

    def test_accepts_list_form_for_max_bots(self):
        entries = parse_registry(
            {
                "MAX_BOTS": ["solo"],
                "MAX_BOT_SOLO_WEBHOOK_SECRET": "s",  # pragma: allowlist secret
                "MAX_BOT_SOLO_API_TOKEN": "t",  # pragma: allowlist secret
            }
        )
        assert entries[0].slug == "solo"


class TestRejectsAmbiguity:
    """Every case here would otherwise be a silent, stable misroute."""

    def test_duplicate_webhook_secret_across_bots(self):
        with pytest.raises(BotRegistryConfigurationError, match="share a webhook secret"):
            parse_registry(
                {
                    "MAX_BOTS": "a,b",
                    "MAX_BOT_A_WEBHOOK_SECRET": "same",  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t1",  # pragma: allowlist secret
                    "MAX_BOT_B_WEBHOOK_SECRET": "same",  # pragma: allowlist secret
                    "MAX_BOT_B_API_TOKEN": "t2",  # pragma: allowlist secret
                }
            )

    def test_error_message_never_contains_the_secret(self):
        with pytest.raises(BotRegistryConfigurationError) as exc:
            parse_registry(
                {
                    "MAX_BOTS": "a,b",
                    "MAX_BOT_A_WEBHOOK_SECRET": "hunter2-topsecret",  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t1",  # pragma: allowlist secret
                    "MAX_BOT_B_WEBHOOK_SECRET": "hunter2-topsecret",  # pragma: allowlist secret
                    "MAX_BOT_B_API_TOKEN": "t2",  # pragma: allowlist secret
                }
            )
        assert "hunter2-topsecret" not in str(exc.value)

    def test_duplicate_slug(self):
        with pytest.raises(BotRegistryConfigurationError, match="duplicate bot slug"):
            parse_registry(
                {
                    "MAX_BOTS": "a,a",
                    "MAX_BOT_A_WEBHOOK_SECRET": "s",  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t",  # pragma: allowlist secret
                }
            )

    # NB: an entirely empty MAX_BOTS is *not* listed here — it means "no
    # registry declared" and is covered by the fallback tests. An empty
    # element *inside* the list is a different thing; see the comma test.
    @pytest.mark.parametrize("slug", ["Client", "sa lon", "salon!", "x" * 33, "salon-bot"])
    def test_invalid_slug(self, slug):
        with pytest.raises(BotRegistryConfigurationError):
            parse_registry({"MAX_BOTS": slug})

    def test_trailing_comma_is_an_error_not_an_empty_bot(self):
        with pytest.raises(BotRegistryConfigurationError, match="empty element"):
            parse_registry(
                {
                    "MAX_BOTS": "a,",
                    "MAX_BOT_A_WEBHOOK_SECRET": "s",  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t",  # pragma: allowlist secret
                }
            )

    def test_missing_webhook_secret(self):
        with pytest.raises(BotRegistryConfigurationError, match="WEBHOOK_SECRET is required"):
            parse_registry(
                {"MAX_BOTS": "a", "MAX_BOT_A_API_TOKEN": "t"}
            )  # pragma: allowlist secret

    def test_missing_api_token(self):
        with pytest.raises(BotRegistryConfigurationError, match="API_TOKEN is required"):
            parse_registry(
                {"MAX_BOTS": "a", "MAX_BOT_A_WEBHOOK_SECRET": "s"}
            )  # pragma: allowlist secret

    def test_invalid_stream(self):
        with pytest.raises(BotRegistryConfigurationError, match="invalid stream"):
            parse_registry(
                {
                    "MAX_BOTS": "a",
                    "MAX_BOT_A_WEBHOOK_SECRET": "s",  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t",  # pragma: allowlist secret
                    "MAX_BOT_A_STREAM": "Max Global",
                }
            )

    def test_non_string_value_rejected(self):
        with pytest.raises(BotRegistryConfigurationError, match="must be strings"):
            parse_registry(
                {
                    "MAX_BOTS": "a",
                    "MAX_BOT_A_WEBHOOK_SECRET": 42,  # pragma: allowlist secret
                    "MAX_BOT_A_API_TOKEN": "t",  # pragma: allowlist secret
                }
            )


class TestLegacyFallback:
    """The property the whole change rests on: unset MAX_BOTS = status quo."""

    def test_synthesizes_global_bot_when_secret_is_in_global_tokens(self):
        # Reproduces the pilot: GLOBAL_BOT_TOKENS contains MAX_WEBHOOK_SECRET,
        # so the legacy bot must route to the tenant-less global stream.
        entries = with_legacy_fallback(
            (),
            webhook_secret=SECRET,  # pragma: allowlist secret
            api_token=TOKEN,  # pragma: allowlist secret
            tenant_slug="formula-tela",
            global_bot_tokens=SECRET,
        )

        assert len(entries) == 1
        assert entries[0].slug == LEGACY_SLUG
        assert entries[0].stream == "max_global"
        assert entries[0].tenant_slug == "formula-tela"

    def test_synthesizes_per_tenant_bot_when_secret_is_not_global(self):
        entries = with_legacy_fallback(
            (),
            webhook_secret=SECRET,  # pragma: allowlist secret
            api_token=TOKEN,  # pragma: allowlist secret
            global_bot_tokens="other,tokens",  # pragma: allowlist secret
        )
        assert entries[0].stream == "max"

    def test_global_tokens_accepts_iterable_form(self):
        entries = with_legacy_fallback(
            (),  # pragma: allowlist secret
            webhook_secret=SECRET,  # pragma: allowlist secret
            api_token=TOKEN,
            global_bot_tokens=[SECRET],  # pragma: allowlist secret
        )
        assert entries[0].stream == "max_global"

    @pytest.mark.parametrize(
        ("secret", "token"),
        [
            ("", ""),  # nothing configured at all — plain dev / CI
            (SECRET, ""),  # secret present, token missing — half-configured
            (None, None),  # settings absent entirely
        ],
    )
    def test_unconfigured_deployment_yields_empty_not_an_error(self, secret, token):
        # A bot that cannot both receive and send is not a bot. Returning ()
        # keeps manage.py working everywhere; raising here would break every
        # developer machine and CI job that has no MAX credentials.
        assert with_legacy_fallback((), webhook_secret=secret, api_token=token) == ()

    def test_explicit_registry_wins_over_legacy(self):
        explicit = parse_registry(TWO_BOTS)
        result = with_legacy_fallback(
            explicit,
            webhook_secret=SECRET,
            api_token=TOKEN,  # pragma: allowlist secret
        )  # pragma: allowlist secret

        assert result == explicit
        assert LEGACY_SLUG not in [e.slug for e in result]


class TestResolution:
    def test_resolves_each_bot_by_its_secret(self):
        registry = parse_registry(TWO_BOTS)

        assert resolve_by_webhook_secret("secret-client", registry).slug == "client"
        assert resolve_by_webhook_secret("secret-salon", registry).slug == "salon"

    def test_unknown_secret_resolves_to_none(self):
        registry = parse_registry(TWO_BOTS)

        assert resolve_by_webhook_secret("secret-nope", registry) is None
        # A prefix of a real secret must not match.
        assert resolve_by_webhook_secret("secret-", registry) is None

    def test_empty_secret_resolves_to_none(self):
        # Guards the dev/CI case where the header is absent: an empty header
        # must never match an empty configured secret into a valid bot.
        assert resolve_by_webhook_secret("", parse_registry(TWO_BOTS)) is None
        assert resolve_by_webhook_secret("", ()) is None

    def test_resolves_by_slug(self):
        registry = parse_registry(TWO_BOTS)

        assert resolve_by_slug("salon", registry).stream == "max_salon"
        assert resolve_by_slug("nope", registry) is None

    def test_api_tokens_in_registry_order(self):
        assert api_tokens(parse_registry(TWO_BOTS)) == ("token-client", "token-salon")
        assert api_tokens(()) == ()


class TestSecretHygiene:
    def test_repr_redacts_credentials(self):
        entry = BotEntry(
            slug="salon",
            webhook_secret="super-secret-value",  # pragma: allowlist secret
            api_token="super-secret-token",  # pragma: allowlist secret
            tenant_slug="formula-tela",
        )
        rendered = repr(entry)

        assert "super-secret-value" not in rendered
        assert "super-secret-token" not in rendered
        assert "<redacted>" in rendered
        # The non-sensitive fields stay visible — the point is a usable repr,
        # not an opaque one.
        assert "salon" in rendered
        assert "formula-tela" in rendered

    def test_entry_is_immutable(self):
        entry = parse_registry(TWO_BOTS)[0]
        with pytest.raises(Exception):
            entry.api_token = "swapped"  # type: ignore[misc]
