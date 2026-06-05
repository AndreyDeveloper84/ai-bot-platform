"""Tests for `apps.channels.max.photo` — Веха 1 of photo adapter port.

Covers:
- `extract_first_photo_url`: empty, None, non-image only, first IMAGE wins
- `download_photo`: happy, >10 MiB cap, timeout, 4xx, 5xx, network error
- SSRF defence (B1 from PR #893 adversarial review): non-https scheme,
  localhost, private/loopback/link-local/multicast/reserved IPs,
  multi-record hostname with one unsafe IP, malformed URL.
  Each reject must NOT invoke httpx — the validator gates before any
  socket opens.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from apps.channels.max.photo import (
    MAX_PHOTO_BYTES,
    PhotoDownloadError,
    PhotoTooLargeError,
    _validate_cdn_url,
    download_photo,
    extract_first_photo_url,
)


# ─── extract_first_photo_url ────────────────────────────────────────────


class TestExtractFirstPhotoUrl:
    def test_empty_list_returns_none(self):
        assert extract_first_photo_url([]) is None

    def test_none_returns_none(self):
        assert extract_first_photo_url(None) is None

    def test_non_image_attachment_returns_none(self):
        """Video / file attachment without any image → None."""
        attachments = [
            {"type": "video", "payload": {"url": "https://x/v.mp4"}},
            {"type": "file", "payload": {"url": "https://x/f.pdf"}},
        ]
        assert extract_first_photo_url(attachments) is None

    def test_first_image_wins_over_later_image(self):
        """Multi-photo message → URL of the first IMAGE in order."""
        attachments = [
            {"type": "image", "payload": {"url": "https://x/first.jpg"}},
            {"type": "image", "payload": {"url": "https://x/second.jpg"}},
        ]
        assert extract_first_photo_url(attachments) == "https://x/first.jpg"

    def test_first_image_after_non_image_wins(self):
        """Mixed attachments: skip video, pick the next image."""
        attachments = [
            {"type": "video", "payload": {"url": "https://x/v.mp4"}},
            {"type": "image", "payload": {"url": "https://x/img.jpg"}},
            {"type": "image", "payload": {"url": "https://x/later.jpg"}},
        ]
        assert extract_first_photo_url(attachments) == "https://x/img.jpg"

    def test_malformed_attachment_skipped(self):
        """Missing payload / non-string url skipped, fall through to next."""
        attachments = [
            {"type": "image"},  # missing payload
            {"type": "image", "payload": None},  # null payload
            {"type": "image", "payload": {"url": None}},  # null url
            {"type": "image", "payload": {"url": ""}},  # empty url
            {"type": "image", "payload": {"url": "https://x/ok.jpg"}},
        ]
        assert extract_first_photo_url(attachments) == "https://x/ok.jpg"

    def test_non_dict_entry_skipped(self):
        """A stray string in the list shouldn't crash the iterator."""
        attachments = [
            "garbage",
            {"type": "image", "payload": {"url": "https://x/y.jpg"}},
        ]
        # mypy would flag this in production but we're testing the
        # tolerance gate; cast through Any.
        assert extract_first_photo_url(attachments) == "https://x/y.jpg"  # type: ignore[arg-type]


# ─── download_photo ─────────────────────────────────────────────────────


class _FakeStreamResponse:
    """Minimal stand-in for `httpx.Response` used inside `Client.stream`."""

    def __init__(self, *, status_code: int, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self._chunks = chunks or []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    """Minimal stand-in for `httpx.Client` — supports the `.stream(...)` ctx mgr."""

    def __init__(self, *, response=None, raise_on_stream=None):
        self._response = response
        self._raise = raise_on_stream
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method: str, url: str):
        self.calls.append((method, url))
        if self._raise is not None:
            raise self._raise
        return self._response


@pytest.fixture(autouse=True)
def _mock_public_dns(monkeypatch):
    """Resolve every hostname in download_photo tests to a public IP.

    The SSRF validator (B1 / PR #893) calls ``socket.getaddrinfo``.
    Real DNS in tests is slow + flaky; tests that want to exercise
    the SSRF reject path use IP literals or monkeypatch this fixture
    inline.
    """

    def _public(host, *_args, **_kwargs):
        # 1.2.3.4 — TEST-NET-3 (RFC 5737) public-addressable, never
        # private/loopback/etc. Stable across test runs.
        return [(2, 1, 6, "", ("1.2.3.4", 0))]

    monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _public)


class TestDownloadPhoto:
    def test_happy_path_concatenates_chunks(self):
        fake = _FakeClient(
            response=_FakeStreamResponse(status_code=200, chunks=[b"hello", b"-", b"world"])
        )
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            out = download_photo("https://cdn.max.test/p.jpg")
        assert out == b"hello-world"
        assert fake.calls == [("GET", "https://cdn.max.test/p.jpg")]

    def test_over_10mib_raises_too_large_mid_stream(self):
        """Cumulative chunks > 10 MiB → raise before fully buffered.

        Stream 6 chunks of 2 MiB each: total = 12 MiB. The cap should
        fire before the 6th chunk is appended.
        """
        big_chunk = b"\x00" * (2 * 1024 * 1024)  # 2 MiB
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[big_chunk] * 6))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoTooLargeError, match="bytes"):
                download_photo("https://cdn.max.test/big.jpg")

    def test_exactly_at_cap_succeeds(self):
        """A photo == MAX_PHOTO_BYTES is accepted (cap is `>` not `>=`)."""
        # Build one chunk == cap.
        big = b"\xff" * MAX_PHOTO_BYTES
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[big]))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            out = download_photo("https://cdn.max.test/edge.jpg")
        assert len(out) == MAX_PHOTO_BYTES

    def test_timeout_raises_download_error(self):
        fake = _FakeClient(raise_on_stream=httpx.ConnectTimeout("slow CDN"))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoDownloadError, match="network"):
                download_photo("https://cdn.max.test/slow.jpg")

    def test_network_error_raises_download_error(self):
        fake = _FakeClient(raise_on_stream=httpx.ConnectError("refused"))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoDownloadError, match="network"):
                download_photo("https://cdn.max.test/dead.jpg")

    def test_cdn_4xx_raises_download_error(self):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=403, chunks=[]))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoDownloadError, match="4xx"):
                download_photo("https://cdn.max.test/forbidden.jpg")

    def test_cdn_5xx_raises_download_error(self):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=503, chunks=[]))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoDownloadError, match="5xx"):
                download_photo("https://cdn.max.test/dead.jpg")

    def test_404_raises_download_error(self):
        """404 is a 4xx — same handling."""
        fake = _FakeClient(response=_FakeStreamResponse(status_code=404, chunks=[]))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with pytest.raises(PhotoDownloadError):
                download_photo("https://cdn.max.test/gone.jpg")


# ─── SSRF validator ─────────────────────────────────────────────────────


class TestValidateCdnUrlScheme:
    """Scheme allow-list — only ``https://`` may pass.

    These cases use IP literals so DNS resolution never runs.
    """

    def test_http_rejected(self):
        with pytest.raises(PhotoDownloadError, match="scheme"):
            _validate_cdn_url("http://1.2.3.4/p.jpg")

    def test_file_rejected(self):
        with pytest.raises(PhotoDownloadError, match="scheme"):
            _validate_cdn_url("file:///etc/passwd")

    def test_gopher_rejected(self):
        with pytest.raises(PhotoDownloadError, match="scheme"):
            _validate_cdn_url("gopher://1.2.3.4/x")

    def test_ftp_rejected(self):
        with pytest.raises(PhotoDownloadError, match="scheme"):
            _validate_cdn_url("ftp://1.2.3.4/x")

    def test_data_uri_rejected(self):
        with pytest.raises(PhotoDownloadError, match="scheme"):
            _validate_cdn_url("data:image/png;base64,AAAA")

    def test_empty_url_rejected(self):
        with pytest.raises(PhotoDownloadError):
            _validate_cdn_url("")

    def test_non_string_url_rejected(self):
        with pytest.raises(PhotoDownloadError):
            _validate_cdn_url(None)  # type: ignore[arg-type]


class TestValidateCdnUrlSpecialNames:
    """Hostname blocklist — ``localhost`` family + cloud metadata names."""

    @pytest.mark.parametrize(
        "host",
        ["localhost", "LOCALHOST", "ip6-localhost", "metadata", "metadata.google.internal"],
    )
    def test_special_name_rejected(self, host):
        with pytest.raises(PhotoDownloadError, match="host not allowed"):
            _validate_cdn_url(f"https://{host}/p.jpg")

    @pytest.mark.parametrize(
        "host",
        ["localhost.", "metadata.", "metadata.google.internal."],
    )
    def test_trailing_dot_bypass_rejected(self, host):
        """B3 from PR #893 re-review: `localhost.` resolves identically to
        `localhost` via getaddrinfo but sails past an exact-match set
        lookup. Validator strips trailing dots before blocklist check.
        """
        with pytest.raises(PhotoDownloadError, match="host not allowed"):
            _validate_cdn_url(f"https://{host}/p.jpg")


class TestValidateCdnUrlIpLiterals:
    """IP-literal hostnames — direct check, no DNS round trip."""

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # AWS / GCE IMDSv1
            "169.254.170.2",  # ECS task metadata
            "127.0.0.1",  # loopback
            "127.5.5.5",  # loopback range
            "10.0.0.1",  # private RFC1918
            "10.255.255.255",
            "172.16.0.1",  # private
            "172.31.255.254",
            "192.168.1.1",  # private
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
            "240.0.0.1",  # reserved
            "::1",  # IPv6 loopback
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # IPv6 ULA private
        ],
    )
    def test_unsafe_ip_literal_rejected(self, ip):
        # `::1` style addresses must be bracketed in URLs.
        host = f"[{ip}]" if ":" in ip else ip
        with pytest.raises(PhotoDownloadError, match="unsafe ip"):
            _validate_cdn_url(f"https://{host}/p.jpg")

    def test_public_ip_literal_passes(self):
        # ``1.2.3.4`` — public-routable per Python's ipaddress module
        # (not in any special-purpose registry). Stable across runs.
        # No DNS resolution because the host IS the literal.
        _validate_cdn_url("https://1.2.3.4/p.jpg")  # no raise


class TestValidateCdnUrlDnsResolution:
    """Hostname → DNS path. Monkeypatches ``socket.getaddrinfo``."""

    def test_hostname_resolves_to_private_ip_rejected(self, monkeypatch):
        """Public-looking hostname that resolves to 10.x → reject."""

        def _private(host, *_args, **_kwargs):
            return [(2, 1, 6, "", ("10.5.5.5", 0))]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _private)
        with pytest.raises(PhotoDownloadError, match="unsafe ip"):
            _validate_cdn_url("https://attacker-controlled.example/p.jpg")

    def test_hostname_resolves_to_loopback_rejected(self, monkeypatch):
        def _loopback(host, *_args, **_kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", 0))]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _loopback)
        with pytest.raises(PhotoDownloadError, match="unsafe ip"):
            _validate_cdn_url("https://evil.example/p.jpg")

    def test_multi_record_one_unsafe_rejects_all(self, monkeypatch):
        """When DNS returns mixed records (public + private), reject whole.

        Attacker controls DNS; can return [1.2.3.4, 127.0.0.1].
        httpx connector picks one at its discretion → we'd lose. Strict
        rule: if ANY record is unsafe, reject the hostname.
        """

        def _mixed(host, *_args, **_kwargs):
            return [
                (2, 1, 6, "", ("8.8.8.8", 0)),  # public
                (2, 1, 6, "", ("127.0.0.1", 0)),  # unsafe — should reject all
            ]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _mixed)
        with pytest.raises(PhotoDownloadError, match="unsafe ip"):
            _validate_cdn_url("https://mixed-records.example/p.jpg")

    def test_dns_lookup_failure_rejects_cleanly(self, monkeypatch):
        """gaierror → PhotoDownloadError, NOT a crash."""

        def _gaierror(host, *_args, **_kwargs):
            import socket

            raise socket.gaierror(0, "no such host")

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _gaierror)
        with pytest.raises(PhotoDownloadError, match="dns"):
            _validate_cdn_url("https://nx.example/p.jpg")

    def test_hostname_resolves_to_public_passes(self, monkeypatch):
        def _public(host, *_args, **_kwargs):
            return [(2, 1, 6, "", ("1.2.3.4", 0))]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _public)
        _validate_cdn_url("https://cdn.max.test/p.jpg")  # no raise


# ─── SSRF defence: rejected URLs MUST NOT open a socket ─────────────────


class TestSSRFRejectionBlocksHttpx:
    """download_photo on a rejected URL must NOT instantiate httpx.

    The fixture-level ``_mock_public_dns`` autouse fixture stubs DNS so
    happy-path tests pass; here we override per-test to put rejects in
    the validator's path and assert ``httpx.Client`` was never called.
    """

    def test_http_url_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="scheme"):
                download_photo("http://1.2.3.4/p.jpg")
        mock_client.assert_not_called()

    def test_file_uri_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="scheme"):
                download_photo("file:///etc/passwd")
        mock_client.assert_not_called()

    def test_aws_metadata_ip_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="unsafe ip"):
                download_photo("https://169.254.169.254/latest/meta-data/")
        mock_client.assert_not_called()

    def test_localhost_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="host not allowed"):
                download_photo("https://localhost:6379/")
        mock_client.assert_not_called()

    def test_private_ip_literal_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="unsafe ip"):
                download_photo("https://10.0.0.1/internal")
        mock_client.assert_not_called()

    def test_hostname_resolving_to_private_does_not_invoke_httpx(self, monkeypatch):
        def _private(host, *_args, **_kwargs):
            return [(2, 1, 6, "", ("10.5.5.5", 0))]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _private)
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError, match="unsafe ip"):
                download_photo("https://evil.example/")
        mock_client.assert_not_called()

    def test_empty_url_does_not_invoke_httpx(self):
        with patch("apps.channels.max.photo.httpx.Client") as mock_client:
            with pytest.raises(PhotoDownloadError):
                download_photo("")
        mock_client.assert_not_called()


# ─── Log redaction: never echo URL / querystring ────────────────────────


class TestLogRedaction:
    """Log lines carry hostname only — never the raw URL or querystring.

    PR #893 P1: MAX CDN URLs commonly include signed query parameters
    (bearer tokens, signatures, expiry). The original `url[:60]` log
    style could leak the signature to any log sink.
    """

    def test_4xx_log_contains_hostname_not_url_path(self, caplog):
        import logging

        fake = _FakeClient(response=_FakeStreamResponse(status_code=403, chunks=[]))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with caplog.at_level(logging.WARNING, logger="apps.channels.max.photo"):
                with pytest.raises(PhotoDownloadError):
                    download_photo("https://cdn.max.test/photo123?sig=SECRET-TOKEN-AAA")
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "cdn.max.test" in log_text
        assert "SECRET-TOKEN-AAA" not in log_text
        assert "photo123" not in log_text
        assert "sig=" not in log_text

    def test_network_error_log_contains_hostname_not_url(self, caplog):
        import logging

        fake = _FakeClient(raise_on_stream=httpx.ConnectTimeout("slow"))
        with patch("apps.channels.max.photo.httpx.Client", return_value=fake):
            with caplog.at_level(logging.WARNING, logger="apps.channels.max.photo"):
                with pytest.raises(PhotoDownloadError):
                    download_photo("https://cdn.max.test/p.jpg?token=SECRET")
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "cdn.max.test" in log_text
        assert "SECRET" not in log_text
