"""Single seam for building Ayla REST URLs (S0-A / #1049).

Ported from Ayla ``core/ayla_urls.py`` so both backends agree on exactly one
rule for turning ``AYLA_BASE_URL`` into a request URL. Historically each
bot-platform client hand-rolled ``f"{AYLA_BASE_URL}/api/v1/..."``; that made
``AYLA_BASE_URL`` ambiguous — is it a bare host, or does it already carry
``/api/v1``? — and a mis-set env var produced a silently-wrong URL
(``.../api/v1/api/v1/...`` or a scheme buried in the path) that only surfaced
as a 404 in production.

``AylaUrlBuilder`` makes the contract explicit and fail-loud:

* ``AYLA_BASE_URL`` is **host-only** — ``scheme://host[:port]`` with no path,
  query, or fragment. Anything else raises at construction.
* the builder — not the caller — inserts the ``api/v1`` version prefix.
* a path that carries its own scheme (``http://…``) is rejected
  (**scheme-in-path**).
* a path that re-states the version prefix is rejected (**double prefix**),
  so ``AYLA_BASE_URL`` accidentally set to ``https://host/api/v1`` cannot
  produce ``/api/v1/api/v1/…``.
* trailing/duplicate slashes are normalised; a single trailing slash on the
  request path is preserved (Ayla's DRF routes are trailing-slash-canonical).

This is the foundation the client-migration stream (S0-B, #1048/#978/#1050)
routes ``profile`` / ``recommendations`` / ``nutrition`` / ``user_proxy`` /
``ayla_payments`` through; ``booking_client`` is wired here as the reference
consumer.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit


# The version prefix the builder owns. ``AYLA_BASE_URL`` must NOT include it.
API_VERSION_PREFIX = "api/v1"

_ALLOWED_SCHEMES = ("http", "https")


class AylaUrlError(ValueError):
    """``AYLA_BASE_URL`` or a request path violates the Ayla URL contract.

    Subclasses :class:`ValueError` so existing ``except ValueError`` fail-fast
    guards in the clients keep catching a bad base URL.
    """


class AylaUrlBuilder:
    """Build ``{host}/api/v1/{path}`` URLs from a host-only ``AYLA_BASE_URL``.

    Construction validates the base is host-only; :meth:`build` validates the
    per-request path. Instances are immutable and cheap — construct one per
    client (or share the module-level cache) and call :meth:`build` per request.
    """

    __slots__ = ("_origin",)

    def __init__(self, base_url: str) -> None:
        self._origin = _normalise_base(base_url)

    @property
    def origin(self) -> str:
        """The validated ``scheme://host[:port]`` origin (no trailing slash)."""
        return self._origin

    @property
    def api_root(self) -> str:
        """``{origin}/api/v1`` — the versioned root, no trailing slash."""
        return f"{self._origin}/{API_VERSION_PREFIX}"

    def build(self, path: str) -> str:
        """Return the absolute URL for ``path`` under ``/api/v1``.

        ``path`` is a resource path *below* the version prefix, e.g.
        ``"internal/services/"`` → ``"{origin}/api/v1/internal/services/"``.
        A single trailing slash is preserved; duplicate/leading slashes are
        collapsed. Each segment is percent-encoded so its contents can never
        restructure the URL (add a query/fragment or escape the host).

        Fails loud on a path that embeds a scheme (``https://…`` / ``//host``),
        re-states the ``api/v1`` prefix, or carries a ``.``/``..`` dot-segment
        that would climb out of the ``/api/v1`` namespace.
        """
        raw = (path or "").strip()
        if not raw:
            raise AylaUrlError("Ayla request path is empty")

        # scheme-in-path: a caller passed a full/absolute URL (``https://…``)
        # or a protocol-relative URL (``//host/…``) where a relative resource
        # path was expected. Reject rather than silently double-host.
        if "://" in raw or raw.startswith("//"):
            raise AylaUrlError(f"Ayla request path must be relative, not a URL: {path!r}")

        segments = [seg for seg in raw.split("/") if seg]
        if not segments:
            raise AylaUrlError(f"Ayla request path has no segments: {path!r}")

        joined = "/".join(segments)
        # double prefix: the path already re-states the version prefix the
        # builder owns (e.g. caller passed "api/v1/internal/..."). Case-fold so
        # ``API/V1`` can't slip a second prefix past the guard.
        low = joined.lower()
        if low == API_VERSION_PREFIX or low.startswith(API_VERSION_PREFIX + "/"):
            raise AylaUrlError(
                f"Ayla request path must not include the {API_VERSION_PREFIX!r} "
                f"prefix (the builder inserts it): {path!r}"
            )

        # dot-segment traversal: ``.``/``..`` survive percent-encoding (dots are
        # unreserved) and RFC-3986 normalisation downstream would let them climb
        # out of ``/api/v1/…``. This is exactly the "silently-wrong URL" the
        # builder exists to prevent — reject it explicitly.
        if any(seg in (".", "..") for seg in segments):
            raise AylaUrlError(f"Ayla request path must not contain '.'/'..' segments: {path!r}")

        # Encode each segment opaquely (``safe=""`` keeps only RFC-3986
        # unreserved chars, so UUIDs/slugs pass through byte-identical) so a
        # reserved char inside a segment (``?`` ``#`` ``/`` …) cannot smuggle a
        # query/fragment or extra path boundary.
        encoded = "/".join(quote(seg, safe="") for seg in segments)
        url = f"{self._origin}/{API_VERSION_PREFIX}/{encoded}"
        # Preserve a single trailing slash — Ayla DRF routes are canonical with
        # it; the segment split above already dropped any duplicates.
        if raw.endswith("/"):
            url += "/"
        return url


def _normalise_base(base_url: str) -> str:
    """Validate ``base_url`` is host-only and return its bare origin.

    Host-only means ``scheme://host[:port]`` — no path, query, or fragment.
    Rejects a missing/unknown scheme, a missing host, an embedded version
    prefix (double-prefix at the base), and any leftover path.
    """
    if not base_url or not base_url.strip():
        raise AylaUrlError("AYLA_BASE_URL is empty — cannot build Ayla URLs")

    stripped = base_url.strip()
    # A backslash is not a valid URL char; some parsers treat it as ``/`` and
    # would re-home the host. Reject before urlsplit hides it in the netloc.
    if "\\" in stripped:
        raise AylaUrlError(f"AYLA_BASE_URL must not contain a backslash: {base_url!r}")

    parts = urlsplit(stripped)

    if parts.scheme not in _ALLOWED_SCHEMES:
        raise AylaUrlError(
            f"AYLA_BASE_URL must start with http:// or https:// (host-only): {base_url!r}"
        )
    if not parts.netloc:
        raise AylaUrlError(f"AYLA_BASE_URL has no host: {base_url!r}")
    if parts.username or parts.password:
        raise AylaUrlError(f"AYLA_BASE_URL must not embed credentials (userinfo): {base_url!r}")
    if parts.query or parts.fragment:
        raise AylaUrlError(f"AYLA_BASE_URL must be host-only (no query/fragment): {base_url!r}")

    path = parts.path.strip("/")
    if path:
        # A path on the base is either an accidental double-prefix
        # (``…/api/v1``) or a stray path — both violate host-only.
        raise AylaUrlError(
            f"AYLA_BASE_URL must be host-only with no path — the builder inserts "
            f"{API_VERSION_PREFIX!r}: {base_url!r}"
        )

    return f"{parts.scheme}://{parts.netloc}"
