"""Strict parser for the ``CSRF_TRUSTED_ORIGINS`` env wiring (DRF-1023).

### Why this module exists

The pilot contour serves the Django admin over HTTPS behind a
TLS-terminating nginx. Django therefore requires every form POST's
``Origin`` header to appear in ``CSRF_TRUSTED_ORIGINS`` — which the
project never declared, so the admin login answered «Ошибка проверки
CSRF» to everyone. The fix is an env-driven setting; this module is the
strict parser behind it, following the same philosophy as
``apps/eventbus/ingest_allowlist.py`` (T-02): a malformed value must
never silently weaken (or silently over-broaden) the CSRF boundary — it
refuses to boot instead.

### Parsing contract

The env value is a CSV of origins, normalized **once** at settings load
into a ``list`` (the type Django expects for ``CSRF_TRUSTED_ORIGINS``):

* empty / unset → ``[]`` (Django's default: no trusted origins)
* surrounding whitespace trimmed per element
* empty elements (``"a,,b"``, trailing comma) rejected
* duplicates collapsed (first occurrence wins, order preserved)
* wildcards (``*``, ``https://*.example.com``, ``all`` …) rejected —
  there is no "trust everything" spelling by construction
* shape: ``http(s)://host[:port]`` only — no path, query, userinfo or
  trailing slash; host is ASCII labels (letters, digits, hyphen, dots)
* scheme and host are lowercased (both case-insensitive per RFC); a
  port, when present, must be 1–65535

Anything else raises :class:`OriginConfigurationError`, which
``config/settings/base.py`` surfaces as ``ImproperlyConfigured`` — a
startup failure, mirroring the T-02 allowlists.
"""

from __future__ import annotations

import re
from typing import Any

# ``scheme://host[:port]`` and nothing else. Host labels are alnum with
# interior hyphens/dots (``api-dev.gobeauty.site``, ``localhost``);
# consecutive dots, leading/trailing hyphens and underscores do not
# parse — a typo'd value fails loudly instead of trusting a junk origin.
_HOST_LABEL = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_ORIGIN_RE = re.compile(
    rf"\Ahttps?://{_HOST_LABEL}(?:\.{_HOST_LABEL})*(?::(?P<port>[0-9]{{1,5}}))?\Z"
)

# Spellings that would mean "everything". Rejected outright rather than
# silently treated as a literal origin — an operator typing one of these
# expects it to widen access, and we must never grant that.
_WILDCARD_TOKENS: frozenset[str] = frozenset({"*", "**", "all", "any", ".*", "%"})


class OriginConfigurationError(ValueError):
    """Raised when the trusted-origins setting cannot be parsed safely.

    Callers MUST treat this as *trust nothing*, never as *trust all*. At
    settings-load time it is re-raised as ``ImproperlyConfigured`` so the
    process refuses to boot.
    """


def parse_trusted_origins(raw: Any) -> list[str]:
    """Parse the ``DJANGO_CSRF_TRUSTED_ORIGINS`` CSV into a list of origins.

    Returns ``[]`` for an unset/empty value — **trust nothing** (Django's
    own default). Raises :class:`OriginConfigurationError` on any
    malformed element; partial acceptance is deliberately not offered,
    because "3 of the 4 origins parsed" is exactly the state where an
    operator believes the contour's origin is trusted while the login
    still 403s (or, worse, a typo'd entry trusts something unintended).
    """
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise OriginConfigurationError(
            f"CSRF_TRUSTED_ORIGINS: value must be a CSV string, got {type(raw).__name__}"
        )
    if not raw.strip():
        return []
    origins: list[str] = []
    for element in (part.strip() for part in raw.split(",")):
        if not element:
            raise OriginConfigurationError(
                "CSRF_TRUSTED_ORIGINS: empty element (check for a stray or trailing comma)"
            )
        if element.lower() in _WILDCARD_TOKENS or "*" in element:
            raise OriginConfigurationError(
                f"CSRF_TRUSTED_ORIGINS: wildcard entry {element!r} is not "
                "permitted — enumerate every trusted origin explicitly"
            )
        match = _ORIGIN_RE.match(element.lower())
        if not match:
            raise OriginConfigurationError(
                f"CSRF_TRUSTED_ORIGINS: {element!r} is not a valid origin "
                "(expected shape: http(s)://host[:port], no path or wildcard)"
            )
        origin = match.group(0)
        port = match.group("port")
        if port is not None and not 1 <= int(port) <= 65535:
            raise OriginConfigurationError(
                f"CSRF_TRUSTED_ORIGINS: {element!r} has an out-of-range port"
            )
        if origin not in origins:
            origins.append(origin)
    return origins
