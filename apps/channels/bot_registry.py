"""Registry of MAX bots served by this deployment (DRF-1061).

### Why this module exists

Until now the platform assumed **exactly one** MAX bot, and that assumption
was spread across four unrelated places:

1. ``apps/ingress/views.py::max_webhook`` gates every webhook on the single
   ``MAX_WEBHOOK_SECRET`` via ``hmac.compare_digest`` — a second bot with its
   own secret is rejected 401 *before* any routing runs.
2. ``apps/channels/max/outbound.py::_token`` sends every reply with the
   single ``MAX_BOT_TOKEN``.
3. ``apps/miniapp_api/auth.py::verify_init_data`` derives the initData HMAC
   key from that same ``MAX_BOT_TOKEN`` and tries exactly **one** key, so a
   Mini App opened from a second bot fails ``bad_signature`` on every screen.
4. ``MAX_BOT_TENANT_SLUG`` binds the Mini App surface to exactly one tenant.

Adding a salon-staff bot meant touching all four. Doing that by copy-paste
(``is_salon_bot_token`` next to ``is_global_bot_token``, a third ``elif``,
another lone ``SALON_BOT_TOKEN``) would have multiplied the assumption by
three instead of removing it once, and made a *fourth* bot cost the same
again. This module makes the bot an explicit, enumerable thing so that the
next bot costs one environment entry and zero lines of code.

### Direction of travel

This is deliberately the same shape the original authors planned, not a
detour. ``apps/ingress/services.py`` and ``config/settings/base.py`` both
document the intended end state — per-tenant multi-token support keyed off
the channel token (Sprint 4 / ADR-0006), eventually read from an encrypted
``Tenant.channel_tokens`` field rather than the environment. Keying this
registry on the **webhook secret** keeps that door open: swapping the
env-backed source for a DB-backed one later changes only :func:`load`.

### Parsing contract

``MAX_BOTS`` is a CSV of bot slugs. Each slug ``<s>`` reads its fields from
``MAX_BOT_<S>_<FIELD>`` (slug upper-cased):

* ``WEBHOOK_SECRET`` — required. The value MAX sends in the
  ``X-Max-Bot-Api-Secret`` header. Doubles as the registry's primary key.
* ``API_TOKEN`` — required. The outbound ``Authorization`` credential, and
  the initData HMAC key.
* ``TENANT_SLUG`` — optional. Empty means tenant-less (the nationwide
  discovery bot, which selects a tenant only at booking time).
* ``STREAM`` — optional, defaults to ``max``. The ingress stream suffix;
  must match a handler registered via ``apps.workers.registry``.
* ``MINIAPP_URL`` / ``WEB_APP`` — optional. Per-bot Mini App address, so a
  bot renders *its own* ``open_app`` button rather than a global one.

Normalization is strict — a malformed value must never silently widen or
misroute access:

* unset / empty ``MAX_BOTS`` → **legacy single-bot fallback** (see below)
* slugs: ``[a-z0-9_]{1,32}``, duplicates rejected
* missing or blank ``WEBHOOK_SECRET`` / ``API_TOKEN`` rejected
* **two bots sharing a webhook secret rejected** — the secret is the only
  routing discriminator, so a collision would make routing ambiguous and,
  worse, silently stable (whichever entry sorted first would win)
* stream: ``[a-z0-9_]{1,32}``

Anything else raises :class:`BotRegistryConfigurationError`, which
``config/settings/base.py`` surfaces as ``ImproperlyConfigured`` so the
process refuses to boot rather than serving a half-configured bot.

### Backward compatibility (load-bearing)

With ``MAX_BOTS`` unset the registry synthesizes **exactly one** entry from
the pre-existing settings, reproducing today's behaviour byte for byte:
``MAX_WEBHOOK_SECRET``, ``MAX_BOT_TOKEN``, ``MAX_BOT_TENANT_SLUG``, and the
``max_global`` stream when the secret appears in ``GLOBAL_BOT_TOKENS``.
Every existing deployment, and the ~900 tests that set those settings
directly, keep working untouched. Removing the legacy names is a separate,
later decision.

### Secrets

Entries hold live credentials. Nothing here ever logs a secret, a token, or
any prefix of one — the ``__repr__`` of :class:`BotEntry` is overridden to
redact both. Comparisons use ``hmac.compare_digest`` only.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Any, Mapping

# Slug shape: lowercase identifier. Used both as the registry key and as a
# fragment of environment-variable names, so anything outside this set would
# either be unreachable via env or collide with the field separator.
_SLUG_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# Ingress stream suffix. `apps.ingress.streams.enqueue` interpolates this into
# `f"{prefix}:{channel}"`, and `apps.workers.reaper` derives `<stream>:dlq`
# from it, so exotic characters would produce unreachable Redis keys.
_STREAM_RE = re.compile(r"^[a-z0-9_]{1,32}$")

_DEFAULT_STREAM = "max"

# The slug used by the legacy fallback entry. Deliberately not a real bot
# name: it marks "this deployment never declared MAX_BOTS".
LEGACY_SLUG = "default"


class BotRegistryConfigurationError(ValueError):
    """Raised when the bot registry cannot be parsed unambiguously.

    Callers MUST treat this as *serve nothing*, never as *serve everything*.
    At settings-load time it is re-raised as ``ImproperlyConfigured`` so the
    process refuses to boot.
    """


@dataclass(frozen=True)
class BotEntry:
    """One MAX bot served by this deployment.

    ``webhook_secret`` is the primary key: it is what MAX puts in the
    ``X-Max-Bot-Api-Secret`` header, which makes it the only value available
    at the moment we must decide *which bot this update belongs to*.
    """

    slug: str
    webhook_secret: str
    api_token: str
    tenant_slug: str = ""
    stream: str = _DEFAULT_STREAM
    miniapp_url: str = ""
    web_app: str = ""

    @property
    def is_tenant_less(self) -> bool:
        """True for the nationwide bot, which resolves a tenant only later."""
        return not self.tenant_slug

    def __repr__(self) -> str:  # pragma: no cover - trivial, but security-relevant
        # Never let a stack trace, a log line, or a debugger session print
        # a live bot token or webhook secret.
        return (
            f"BotEntry(slug={self.slug!r}, webhook_secret='<redacted>', "
            f"api_token='<redacted>', tenant_slug={self.tenant_slug!r}, "
            f"stream={self.stream!r}, miniapp_url={self.miniapp_url!r})"
        )


def _clean(raw: Any) -> str:
    """Coerce a settings/env value to a trimmed string. ``None`` → ``""``."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise BotRegistryConfigurationError(
            f"bot registry values must be strings, got {type(raw).__name__}"
        )
    return raw.strip()


def _split_slugs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        return [element.strip() for element in raw.split(",")]
    if isinstance(raw, (list, tuple)):  # noqa: UP038 — explicit tuple
        out: list[str] = []
        for element in raw:
            if not isinstance(element, str):
                raise BotRegistryConfigurationError(
                    f"MAX_BOTS elements must be strings, got {type(element).__name__}"
                )
            out.append(element.strip())
        return out
    raise BotRegistryConfigurationError(
        f"MAX_BOTS must be a CSV string or a list of strings, got {type(raw).__name__}"
    )


def parse_registry(env: Mapping[str, Any]) -> tuple[BotEntry, ...]:
    """Build the registry from an environment-like mapping.

    Args:
      env: Usually ``os.environ``. Any ``Mapping[str, str]`` works, which is
        what makes this testable without touching the process environment.

    Returns:
      Entries in declaration order. Empty ``MAX_BOTS`` returns ``()`` — the
      caller decides whether to apply the legacy fallback (see
      :func:`with_legacy_fallback`), because only the settings module knows
      the legacy values.

    Raises:
      BotRegistryConfigurationError: on any ambiguity. See module docstring.
    """

    slugs = _split_slugs(env.get("MAX_BOTS"))
    if not slugs:
        return ()

    entries: list[BotEntry] = []
    seen_slugs: set[str] = set()
    # Secret → slug, to reject collisions with a message that names both
    # bots without ever printing the colliding secret.
    seen_secrets: dict[str, str] = {}

    for slug in slugs:
        if not slug:
            raise BotRegistryConfigurationError(
                "MAX_BOTS contains an empty element — check for a stray or trailing comma"
            )
        if not _SLUG_RE.match(slug):
            raise BotRegistryConfigurationError(
                f"MAX_BOTS: invalid bot slug {slug!r} — expected lowercase "
                "[a-z0-9_], 1-32 characters"
            )
        if slug in seen_slugs:
            raise BotRegistryConfigurationError(f"MAX_BOTS: duplicate bot slug {slug!r}")
        seen_slugs.add(slug)

        prefix = f"MAX_BOT_{slug.upper()}_"
        webhook_secret = _clean(env.get(f"{prefix}WEBHOOK_SECRET"))
        api_token = _clean(env.get(f"{prefix}API_TOKEN"))

        if not webhook_secret:
            raise BotRegistryConfigurationError(
                f"bot {slug!r}: {prefix}WEBHOOK_SECRET is required — refusing to "
                "register a bot whose webhooks could not be authenticated"
            )
        if not api_token:
            raise BotRegistryConfigurationError(
                f"bot {slug!r}: {prefix}API_TOKEN is required — a bot that cannot "
                "send is a bot that receives messages and silently drops them"
            )

        if webhook_secret in seen_secrets:
            raise BotRegistryConfigurationError(
                f"bots {seen_secrets[webhook_secret]!r} and {slug!r} share a webhook "
                "secret — the secret is the only routing discriminator, so this would "
                "silently misroute every update for one of them"
            )
        seen_secrets[webhook_secret] = slug

        stream = _clean(env.get(f"{prefix}STREAM")) or _DEFAULT_STREAM
        if not _STREAM_RE.match(stream):
            raise BotRegistryConfigurationError(
                f"bot {slug!r}: invalid stream {stream!r} — expected lowercase "
                "[a-z0-9_], 1-32 characters"
            )

        entries.append(
            BotEntry(
                slug=slug,
                webhook_secret=webhook_secret,
                api_token=api_token,
                tenant_slug=_clean(env.get(f"{prefix}TENANT_SLUG")),
                stream=stream,
                miniapp_url=_clean(env.get(f"{prefix}MINIAPP_URL")),
                web_app=_clean(env.get(f"{prefix}WEB_APP")),
            )
        )

    return tuple(entries)


def with_legacy_fallback(
    entries: tuple[BotEntry, ...],
    *,
    webhook_secret: Any,
    api_token: Any,
    tenant_slug: Any = "",
    global_bot_tokens: Any = "",
    miniapp_url: Any = "",
    web_app: Any = "",
) -> tuple[BotEntry, ...]:
    """Return ``entries``, or synthesize the single legacy bot when empty.

    This is what keeps the change additive. A deployment that never heard of
    ``MAX_BOTS`` — including every current environment and the large body of
    tests that set ``MAX_BOT_TOKEN`` via ``override_settings`` — continues to
    behave exactly as before.

    The legacy entry's stream reproduces the existing branch in
    ``apps/ingress/views.py``: ``max_global`` when the webhook secret is
    listed in ``GLOBAL_BOT_TOKENS`` (the pilot's configuration), ``max``
    otherwise.

    A deployment with no webhook secret yields ``()``: that is dev/CI, where
    the gate rejected everything anyway, and raising here would break
    ``manage.py`` for everyone.

    **The secret alone is enough** to synthesize the entry — a missing
    ``api_token`` does not suppress it. This mirrors the behaviour being
    replaced exactly: the old gate compared the header against
    ``MAX_WEBHOOK_SECRET`` and never consulted the bot token, so a
    deployment with a secret but no token accepted webhooks and only failed
    later, at send time. Requiring both here would turn that into a silent
    401 on ingest — a behaviour change disguised as a refactor. Declared
    bots are held to the stricter rule (see :func:`parse_registry`), because
    there the configuration is explicit and can be complete.
    """

    if entries:
        return entries

    secret = _clean(webhook_secret)
    token = _clean(api_token)
    if not secret:
        return ()

    raw_global = global_bot_tokens
    if isinstance(raw_global, str):
        global_set = {t.strip() for t in raw_global.split(",") if t.strip()}
    elif isinstance(raw_global, (list, tuple, set, frozenset)):  # noqa: UP038
        global_set = {str(t).strip() for t in raw_global if str(t).strip()}
    else:
        global_set = set()

    return (
        BotEntry(
            slug=LEGACY_SLUG,
            webhook_secret=secret,
            api_token=token,
            tenant_slug=_clean(tenant_slug),
            stream="max_global" if secret in global_set else _DEFAULT_STREAM,
            miniapp_url=_clean(miniapp_url),
            web_app=_clean(web_app),
        ),
    )


def effective_registry() -> tuple[BotEntry, ...]:
    """The registry as it applies to the *current* settings.

    Why this is not simply ``settings.MAX_BOT_REGISTRY``: that value is
    computed once, when the settings module is imported. Anything that
    changes the legacy settings afterwards — ``override_settings`` in the
    large body of existing tests, a settings object assembled by hand — would
    otherwise be invisible here, and the webhook gate would answer 401 to
    requests the old code accepted.

    So: prefer the parsed registry when a deployment declared one, and
    otherwise synthesize the legacy entry from whatever the legacy settings
    say *right now*. This is what makes the multi-bot gate a drop-in
    replacement for the single-secret gate rather than a behavioural change.
    """

    from django.conf import settings

    declared = getattr(settings, "MAX_BOT_REGISTRY", ()) or ()
    if declared:
        return tuple(declared)

    return with_legacy_fallback(
        (),
        webhook_secret=getattr(settings, "MAX_WEBHOOK_SECRET", ""),
        api_token=getattr(settings, "MAX_BOT_TOKEN", ""),
        # Ingress resolves a legacy bot's tenant from the channel-token map,
        # not from MAX_BOT_TENANT_SLUG — see config/settings/base.py.
        tenant_slug="",
        global_bot_tokens=getattr(settings, "GLOBAL_BOT_TOKENS", ""),
        miniapp_url=getattr(settings, "MAX_MINIAPP_URL", ""),
        web_app=getattr(settings, "MAX_BOT_WEB_APP", ""),
    )


def resolve_by_webhook_secret(secret: str, registry: tuple[BotEntry, ...]) -> BotEntry | None:
    """Find the bot a webhook belongs to, in constant time w.r.t. the match.

    Every entry is compared with ``hmac.compare_digest`` and the loop does
    **not** break early. An early ``return`` would leak, through response
    timing, which position in the registry matched — and with it a hint about
    which secret an attacker's guess is closest to.

    Returns ``None`` when nothing matches; the caller answers 401.
    """

    if not secret:
        return None

    encoded = secret.encode("utf-8")
    found: BotEntry | None = None
    for entry in registry:
        if hmac.compare_digest(encoded, entry.webhook_secret.encode("utf-8")):
            found = entry
    return found


def resolve_by_slug(slug: str, registry: tuple[BotEntry, ...]) -> BotEntry | None:
    """Look a bot up by slug. Plain equality — a slug is not a secret."""

    for entry in registry:
        if entry.slug == slug:
            return entry
    return None


def resolve_by_tenant_stream(
    tenant_slug: str, stream: str, registry: tuple[BotEntry, ...]
) -> BotEntry | None:
    """The bot serving ``tenant_slug`` on ``stream``, if any.

    Both parts are required. Tenant alone is not enough: nothing forbids a
    salon from having a per-tenant client bot (``stream=max``) as well as a
    staff bot, and picking the wrong one sends staff messages from the
    customer-facing token — which, since MAX chat ids are per-bot, most
    likely fails outright rather than merely looking odd.

    Returns ``None`` when the deployment has no such bot; callers must
    treat that as "do not speak as anyone" rather than falling back to a
    default identity.
    """

    if not tenant_slug or not stream:
        return None
    for entry in registry:
        if entry.tenant_slug == tenant_slug and entry.stream == stream:
            return entry
    return None


def api_tokens(registry: tuple[BotEntry, ...]) -> tuple[str, ...]:
    """Every outbound/HMAC token, in registry order.

    Used by initData verification, which must try each candidate key: the
    Mini App is opened *from a bot*, so its payload is signed with that
    bot's token, and the request itself carries no bot identifier.
    """

    return tuple(entry.api_token for entry in registry)
