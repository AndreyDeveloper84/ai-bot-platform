"""Длительность Ogg/Opus по granule position. Страницы собираются вручную — без ffmpeg."""

from __future__ import annotations

import struct

import pytest

from apps.speech.ogg import OPUS_RATE, ogg_duration_s


def page(granule: int, payload: bytes, *, seq: int = 0, flags: int = 0) -> bytes:
    """Одна страница Ogg (RFC 3533 §6). CRC не считаем — парсер его не проверяет."""
    segments = bytes([len(payload)]) if payload else b"\x00"
    return (
        b"OggS"
        + b"\x00"
        + bytes([flags])
        + struct.pack("<q", granule)
        + struct.pack("<I", 0x1234)
        + struct.pack("<I", seq)
        + b"\x00\x00\x00\x00"
        + bytes([len(segments)])
        + segments
        + payload
    )


def opus_head(pre_skip: int) -> bytes:
    return (
        b"OpusHead"
        + b"\x01"
        + b"\x01"
        + struct.pack("<H", pre_skip)
        + struct.pack("<I", 48000)
        + b"\x00\x00"
        + b"\x00"
    )


def test_duration_from_last_page_minus_pre_skip() -> None:
    data = page(0, opus_head(312), flags=2) + page(48_000, b"x", seq=1) + page(169_272, b"y", seq=2)
    assert ogg_duration_s(data) == pytest.approx((169_272 - 312) / OPUS_RATE)


def test_skips_pages_with_unknown_granule() -> None:
    # −1 = пакет продолжается на следующей странице; последняя завершённая — 96000
    data = page(0, opus_head(0), flags=2) + page(96_000, b"x", seq=1) + page(-1, b"y", seq=2)
    assert ogg_duration_s(data) == pytest.approx(2.0)


def test_pre_skip_larger_than_granule_clamps_to_zero() -> None:
    data = page(0, opus_head(1000), flags=2) + page(500, b"x", seq=1)
    assert ogg_duration_s(data) == 0.0


def test_without_opus_head_pre_skip_is_zero() -> None:
    data = page(0, b"not-opus", flags=2) + page(48_000, b"x", seq=1)
    assert ogg_duration_s(data) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "data", [b"", b"ID3\x03", b"RIFF....WAVE", b"OggS", b"OggS" + b"\x00" * 10]
)
def test_unreadable_returns_none(data: bytes) -> None:
    assert ogg_duration_s(data) is None


def test_truncated_tail_still_reads_previous_page() -> None:
    full = (
        page(0, opus_head(0), flags=2) + page(48_000, b"x", seq=1) + page(96_000, b"y" * 50, seq=2)
    )
    truncated = full[: len(full) - 40]  # последняя страница обрезана, но заголовок цел
    assert ogg_duration_s(truncated) == pytest.approx(2.0)
