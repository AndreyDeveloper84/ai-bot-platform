"""Unit tests for :class:`AylaUrlBuilder` (S0-A / #1049).

Locks the single URL contract both backends share:

* ``AYLA_BASE_URL`` is host-only (``scheme://host[:port]``, no path);
* the builder inserts the ``api/v1`` prefix — callers never do;
* scheme-in-path and double ``api/v1`` prefix are rejected, not silently
  mangled into a wrong-but-plausible URL;
* trailing / duplicate slashes are normalised.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.url_builder import (
    API_VERSION_PREFIX,
    AylaUrlBuilder,
    AylaUrlError,
)


# ─── construction: host-only base ────────────────────────────────────────────


class TestHostOnlyBase:
    def test_bare_host_accepted(self) -> None:
        b = AylaUrlBuilder("https://gobeauty.site")
        assert b.origin == "https://gobeauty.site"
        assert b.api_root == "https://gobeauty.site/api/v1"

    def test_host_with_port_accepted(self) -> None:
        b = AylaUrlBuilder("http://localhost:8000")
        assert b.origin == "http://localhost:8000"

    def test_trailing_slash_on_base_normalised(self) -> None:
        # A lone trailing slash is a host, not a path — accepted + stripped.
        assert AylaUrlBuilder("https://ayla.test/").origin == "https://ayla.test"

    def test_surrounding_whitespace_stripped(self) -> None:
        assert AylaUrlBuilder("  https://ayla.test  ").origin == "https://ayla.test"

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_base_rejected(self, bad: str | None) -> None:
        with pytest.raises(AylaUrlError, match="AYLA_BASE_URL is empty"):
            AylaUrlBuilder(bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "bad",
        ["gobeauty.site", "ftp://ayla.test", "//ayla.test", "ayla.test:8000"],
    )
    def test_missing_or_bad_scheme_rejected(self, bad: str) -> None:
        with pytest.raises(AylaUrlError):
            AylaUrlBuilder(bad)

    def test_base_with_path_rejected_as_not_host_only(self) -> None:
        with pytest.raises(AylaUrlError, match="host-only"):
            AylaUrlBuilder("https://ayla.test/some/path")

    def test_base_with_api_prefix_rejected_double_prefix(self) -> None:
        # AYLA_BASE_URL accidentally set to the api root — the classic
        # double-prefix trap the builder exists to prevent.
        with pytest.raises(AylaUrlError, match="host-only"):
            AylaUrlBuilder("https://ayla.test/api/v1")

    def test_base_with_query_rejected(self) -> None:
        with pytest.raises(AylaUrlError, match="host-only"):
            AylaUrlBuilder("https://ayla.test?debug=1")

    def test_base_with_userinfo_rejected(self) -> None:
        with pytest.raises(AylaUrlError, match="credentials"):
            AylaUrlBuilder("https://user:pass@ayla.test")  # pragma: allowlist secret

    def test_base_with_backslash_rejected(self) -> None:
        with pytest.raises(AylaUrlError, match="backslash"):
            AylaUrlBuilder("https://ayla.test\\evil.test")

    def test_ipv6_host_with_port_accepted(self) -> None:
        b = AylaUrlBuilder("http://[::1]:8000")
        assert b.build("internal/services/") == "http://[::1]:8000/api/v1/internal/services/"


# ─── build(): path handling ──────────────────────────────────────────────────


class TestBuild:
    def _b(self) -> AylaUrlBuilder:
        return AylaUrlBuilder("https://ayla.test")

    def test_inserts_api_prefix(self) -> None:
        assert self._b().build("internal/services/") == (
            "https://ayla.test/api/v1/internal/services/"
        )

    def test_preserves_single_trailing_slash(self) -> None:
        assert self._b().build("internal/appointments/").endswith("/appointments/")

    def test_no_trailing_slash_preserved_when_absent(self) -> None:
        assert self._b().build("internal/services") == (
            "https://ayla.test/api/v1/internal/services"
        )

    def test_leading_slash_on_path_collapsed(self) -> None:
        assert self._b().build("/internal/services/") == (
            "https://ayla.test/api/v1/internal/services/"
        )

    def test_duplicate_internal_slashes_collapsed(self) -> None:
        assert self._b().build("internal//me///bookings/") == (
            "https://ayla.test/api/v1/internal/me/bookings/"
        )

    def test_nested_path_with_ids(self) -> None:
        assert self._b().build("internal/specialists/spec-1/slots/") == (
            "https://ayla.test/api/v1/internal/specialists/spec-1/slots/"
        )

    @pytest.mark.parametrize("bad", ["", "   ", "/", "///"])
    def test_empty_path_rejected(self, bad: str) -> None:
        with pytest.raises(AylaUrlError):
            self._b().build(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "https://evil.test/steal",
            "http://ayla.test/internal/services/",
            "//evil.test/x",
            "internal/x?redirect=http://evil.test",
        ],
    )
    def test_scheme_in_path_rejected(self, bad: str) -> None:
        with pytest.raises(AylaUrlError, match="not a URL"):
            self._b().build(bad)

    @pytest.mark.parametrize(
        "bad",
        ["api/v1/internal/services/", "api/v1", "/api/v1/internal/x", "API/V1/internal/x"],
    )
    def test_double_prefix_rejected(self, bad: str) -> None:
        # Case-insensitive: ``API/V1`` must not slip a second prefix past.
        with pytest.raises(AylaUrlError, match="prefix"):
            self._b().build(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "internal/../../admin/",
            "internal/appointments/../../../secret/",
            "internal/./services/",
            "..",
            "internal/..",
        ],
    )
    def test_dot_segment_traversal_rejected(self, bad: str) -> None:
        # ``.``/``..`` survive percent-encoding and would climb out of
        # ``/api/v1/internal/`` under downstream RFC-3986 normalisation.
        with pytest.raises(AylaUrlError, match=r"\.\."):
            self._b().build(bad)

    def test_reserved_chars_in_segment_are_encoded_not_structural(self) -> None:
        # A ``?`` inside a segment must become %3F (literal), not start a query;
        # otherwise it could merge with the client's httpx ``params=``.
        assert self._b().build("internal/a?b/") == "https://ayla.test/api/v1/internal/a%3Fb/"
        # ``#`` → %23 (cannot become a fragment); space → %20.
        assert self._b().build("internal/a b#c/") == "https://ayla.test/api/v1/internal/a%20b%23c/"

    def test_uuid_and_slug_segments_pass_through_byte_identical(self) -> None:
        # Encoding must be a no-op for the real Ayla id/slug shapes so the built
        # URL stays byte-identical to the legacy f-string.
        assert self._b().build("internal/specialists/3f2a1b4c-dead-beef-0011-223344556677/") == (
            "https://ayla.test/api/v1/internal/specialists/3f2a1b4c-dead-beef-0011-223344556677/"
        )

    def test_segment_named_like_scheme_not_falsely_rejected(self) -> None:
        # A resource segment that merely starts with "http" (e.g. "httpbin")
        # must NOT be mistaken for a scheme-in-path.
        assert self._b().build("internal/httpbin/") == (
            "https://ayla.test/api/v1/internal/httpbin/"
        )


def test_api_version_prefix_constant() -> None:
    assert API_VERSION_PREFIX == "api/v1"
