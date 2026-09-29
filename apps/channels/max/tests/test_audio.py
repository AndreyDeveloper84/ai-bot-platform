"""Tests for ``apps.channels.max.audio`` (DRF-1942, этап 1, PR 1).

Mirrors ``test_photo.py`` in shape. No network: ``httpx.Client`` is
patched by name inside ``apps.channels.max.audio``; DNS is patched
inside ``apps.channels.max.photo`` because that is where the shared
SSRF validator lives.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

import httpx
import pytest

from apps.channels.max.audio import (
    AUDIO_DOWNLOAD_DEADLINE_S,
    MAX_AUDIO_BYTES,
    AudioDownloadError,
    AudioFormatError,
    AudioRef,
    AudioTooLargeError,
    download_audio,
    extract_first_audio,
    is_ogg,
)

OGG_HEAD = b"OggS\x00\x02" + b"\x00" * 20  # capture pattern + fake page header


# ─── extract_first_audio ─────────────────────────────────────────────


class TestExtractFirstAudio:
    def test_none_and_empty(self):
        assert extract_first_audio(None) is None
        assert extract_first_audio([]) is None

    def test_live_shape_from_stage0(self):
        # Exactly what the 17.09 webhook carried (values replaced).
        atts = [
            {
                "type": "audio",
                "payload": {
                    "id": 4318852874390,
                    "url": "https://a.oneme.ru/x?sig=1",
                    "token": "t",
                },
            }
        ]
        ref = extract_first_audio(atts)
        assert ref == AudioRef(url="https://a.oneme.ru/x?sig=1", attachment_id=4318852874390)

    def test_picks_audio_among_mixed(self):
        atts = [
            {"type": "image", "payload": {"url": "https://a/img"}},
            {"type": "audio", "payload": {"url": "https://a/voice"}},
        ]
        assert extract_first_audio(atts) == AudioRef(url="https://a/voice", attachment_id=None)

    def test_first_audio_wins(self):
        atts = [
            {"type": "audio", "payload": {"url": "https://a/one", "id": 1}},
            {"type": "audio", "payload": {"url": "https://a/two", "id": 2}},
        ]
        assert extract_first_audio(atts).url == "https://a/one"

    @pytest.mark.parametrize(
        "atts",
        [
            [{"type": "image", "payload": {"url": "https://a/img"}}],
            [{"type": "audio"}],
            [{"type": "audio", "payload": "not-a-dict"}],
            [{"type": "audio", "payload": {"url": ""}}],
            [{"type": "audio", "payload": {"url": 123}}],
            ["garbage", None, 42],
        ],
    )
    def test_malformed_or_absent_returns_none(self, atts):
        assert extract_first_audio(atts) is None

    def test_skips_malformed_then_finds_valid(self):
        atts = [
            {"type": "audio", "payload": {}},
            {"type": "audio", "payload": {"url": "https://a/v"}},
        ]
        assert extract_first_audio(atts).url == "https://a/v"

    @pytest.mark.parametrize("raw_id", ["4318852874390", 12.5, True, None])
    def test_non_int_id_becomes_none(self, raw_id):
        atts = [{"type": "audio", "payload": {"url": "https://a/v", "id": raw_id}}]
        assert extract_first_audio(atts).attachment_id is None


# ─── is_ogg ──────────────────────────────────────────────────────────


class TestIsOgg:
    def test_ogg(self):
        assert is_ogg(OGG_HEAD)

    @pytest.mark.parametrize("head", [b"", b"Ogg", b"ID3\x03", b"RIFF....WAVE", b"\xff\xfb\x90"])
    def test_not_ogg(self, head):
        assert is_ogg(head) is False


# ─── download_audio ──────────────────────────────────────────────────


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int,
        chunks: list[bytes] | None = None,
        sleep_s: float = 0.0,
    ):
        self.status_code = status_code
        self._chunks = chunks or []
        self._sleep_s = sleep_s

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        for chunk in self._chunks:
            if self._sleep_s:
                time.sleep(self._sleep_s)
            yield chunk


class _FakeClient:
    def __init__(self, *, response=None, raise_on_stream=None):
        self._response = response
        self._raise = raise_on_stream
        self.calls: list[tuple[str, str]] = []
        self.kwargs: dict = {}

    def __call__(self, **kwargs):
        # `httpx.Client(timeout=..., follow_redirects=...)` — capture kwargs.
        self.kwargs = kwargs
        return self

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
    def _public(host, *_args, **_kwargs):
        return [(2, 1, 6, "", ("1.2.3.4", 0))]

    monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _public)


def _client(fake: _FakeClient):
    return patch("apps.channels.max.audio.httpx.Client", new=fake)


class TestDownloadAudio:
    def test_happy_path_returns_joined_ogg_bytes(self):
        fake = _FakeClient(
            response=_FakeStreamResponse(status_code=200, chunks=[OGG_HEAD, b"abc", b"def"])
        )
        with _client(fake):
            out = download_audio("https://a.oneme.ru/v.ogg?sig=x")
        assert out == OGG_HEAD + b"abcdef"
        assert fake.calls == [("GET", "https://a.oneme.ru/v.ogg?sig=x")]

    def test_no_redirects_and_deadline_as_httpx_timeout(self):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[OGG_HEAD]))
        with _client(fake):
            download_audio("https://a.oneme.ru/v.ogg")
        assert fake.kwargs["follow_redirects"] is False
        assert fake.kwargs["timeout"] == AUDIO_DOWNLOAD_DEADLINE_S

    @pytest.mark.parametrize("status", [400, 403, 404, 410])
    def test_4xx_raises_download_error(self, status):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=status))
        with _client(fake), pytest.raises(AudioDownloadError, match="cdn 4xx"):
            download_audio("https://a.oneme.ru/v.ogg")

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx_raises_download_error(self, status):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=status))
        with _client(fake), pytest.raises(AudioDownloadError, match="cdn 5xx"):
            download_audio("https://a.oneme.ru/v.ogg")

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
        ],
    )
    def test_network_errors_wrapped(self, exc):
        fake = _FakeClient(raise_on_stream=exc)
        with _client(fake), pytest.raises(AudioDownloadError, match="network"):
            download_audio("https://a.oneme.ru/v.ogg")

    def test_unexpected_httpx_error_wrapped(self):
        fake = _FakeClient(raise_on_stream=httpx.InvalidURL("bad"))
        with _client(fake), pytest.raises(AudioDownloadError, match="unexpected"):
            download_audio("https://a.oneme.ru/v.ogg")

    def test_too_large_aborts_mid_stream(self):
        half = MAX_AUDIO_BYTES // 2 + 1
        chunks = [OGG_HEAD + b"\x00" * half, b"\x00" * half]  # crosses cap on chunk 2
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=chunks))
        with _client(fake), pytest.raises(AudioTooLargeError):
            download_audio("https://a.oneme.ru/v.ogg")

    def test_exactly_at_cap_is_allowed(self):
        body = OGG_HEAD + b"\x00" * (MAX_AUDIO_BYTES - len(OGG_HEAD))
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[body]))
        with _client(fake):
            assert len(download_audio("https://a.oneme.ru/v.ogg")) == MAX_AUDIO_BYTES

    @pytest.mark.parametrize("first", [b"ID3\x03\x00\x00", b"RIFF\x00\x00\x00\x00WAVE", b"<html>"])
    def test_not_ogg_refused_on_first_chunk(self, first):
        fake = _FakeClient(
            response=_FakeStreamResponse(status_code=200, chunks=[first, b"\x00" * 100])
        )
        with _client(fake), pytest.raises(AudioFormatError):
            download_audio("https://a.oneme.ru/v.mp3")

    def test_empty_body_is_format_error(self):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[]))
        with _client(fake), pytest.raises(AudioFormatError):
            download_audio("https://a.oneme.ru/v.ogg")

    def test_leading_empty_chunk_then_ogg_is_fine(self):
        fake = _FakeClient(
            response=_FakeStreamResponse(status_code=200, chunks=[b"", OGG_HEAD, b"x"])
        )
        with _client(fake):
            assert download_audio("https://a.oneme.ru/v.ogg") == OGG_HEAD + b"x"

    def test_wall_clock_deadline_fires_even_when_each_read_is_fast(self):
        # Each read takes 30 ms — well under any per-operation timeout —
        # but the whole transfer exceeds a 50 ms budget.
        fake = _FakeClient(
            response=_FakeStreamResponse(
                status_code=200, chunks=[OGG_HEAD, b"a", b"b", b"c"], sleep_s=0.03
            )
        )
        with _client(fake), pytest.raises(AudioDownloadError, match="deadline"):
            download_audio("https://a.oneme.ru/v.ogg", deadline_s=0.05)


class TestSSRFRejectionBlocksHttpx:
    @pytest.mark.parametrize(
        "url",
        [
            "http://a.oneme.ru/v.ogg",
            "file:///etc/passwd",
            "https://169.254.169.254/latest/meta-data/",
            "https://127.0.0.1/v.ogg",
            "https://localhost/v.ogg",
            "https://[::1]/v.ogg",
            "",
            "not a url",
        ],
    )
    def test_rejected_before_any_socket(self, url):
        with patch("apps.channels.max.audio.httpx.Client") as mock_client:
            with pytest.raises(AudioDownloadError):
                download_audio(url)
        mock_client.assert_not_called()

    def test_hostname_resolving_to_private_rejected(self, monkeypatch):
        def _private(host, *_args, **_kwargs):
            return [(2, 1, 6, "", ("10.0.0.5", 0))]

        monkeypatch.setattr("apps.channels.max.photo.socket.getaddrinfo", _private)
        with patch("apps.channels.max.audio.httpx.Client") as mock_client:
            with pytest.raises(AudioDownloadError):
                download_audio("https://evil.example/v.ogg")
        mock_client.assert_not_called()


class TestLogRedaction:
    """Log lines carry the hostname only — the signed query is a 24 h bearer for a voice."""

    def _assert_clean(self, caplog):
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "a.oneme.ru" in text
        for secret in ("SECRET-SIG", "signatureToken", "userId=777", "voice123"):
            assert secret not in text
        return text

    URL = "https://a.oneme.ru/voice123?signatureToken=SECRET-SIG&userId=777"

    def test_4xx(self, caplog):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=403))
        with (
            _client(fake),
            caplog.at_level(logging.WARNING, logger="apps.channels.max.audio"),
        ):
            with pytest.raises(AudioDownloadError):
                download_audio(self.URL)
        self._assert_clean(caplog)

    def test_network(self, caplog):
        fake = _FakeClient(raise_on_stream=httpx.ConnectTimeout("slow"))
        with (
            _client(fake),
            caplog.at_level(logging.WARNING, logger="apps.channels.max.audio"),
        ):
            with pytest.raises(AudioDownloadError):
                download_audio(self.URL)
        self._assert_clean(caplog)

    def test_not_ogg(self, caplog):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=200, chunks=[b"ID3\x03"]))
        with (
            _client(fake),
            caplog.at_level(logging.WARNING, logger="apps.channels.max.audio"),
        ):
            with pytest.raises(AudioFormatError):
                download_audio(self.URL)
        self._assert_clean(caplog)

    def test_deadline(self, caplog):
        fake = _FakeClient(
            response=_FakeStreamResponse(status_code=200, chunks=[OGG_HEAD, b"a"], sleep_s=0.03)
        )
        with (
            _client(fake),
            caplog.at_level(logging.WARNING, logger="apps.channels.max.audio"),
        ):
            with pytest.raises(AudioDownloadError):
                download_audio(self.URL, deadline_s=0.01)
        self._assert_clean(caplog)

    def test_exception_messages_carry_no_url(self):
        fake = _FakeClient(response=_FakeStreamResponse(status_code=404))
        with _client(fake), pytest.raises(AudioDownloadError) as info:
            download_audio(self.URL)
        message = str(info.value)
        assert message == "cdn 4xx: HTTP 404"  # presence: the message is the status, nothing else
        assert "SECRET-SIG" not in message
        assert "voice123" not in message
