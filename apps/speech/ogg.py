"""Длительность Ogg/Opus без декодера — по granule position последней страницы.

Зачем считать самим: ответ провайдера длительности не содержит, а лимит
длины нужно проверить **до** отправки — чтобы не платить за то, что всё
равно откажем (план этапа 1, PR 2).

Формат (RFC 3533, RFC 7845): каждая страница начинается с ``OggS``, на
смещении 6 лежит 64-битный granule position; у Opus это число отсчётов
на 48 кГц независимо от исходной частоты. Первая страница несёт
``OpusHead`` с ``pre_skip`` — столько отсчётов декодер отбрасывает в
начале. Длительность = (последний granule − pre_skip) / 48000.

CRC страниц не проверяется: файл пришёл от CDN MAX и уже прошёл проверку
``OggS`` в ``audio.py``; нам нужна оценка длины, а не валидация.
"""

from __future__ import annotations

import struct
from typing import Final

OPUS_RATE: Final[int] = 48_000
_PAGE_MAGIC: Final[bytes] = b"OggS"
_HEAD_MAGIC: Final[bytes] = b"OpusHead"
_MIN_PAGE_HEADER: Final[int] = 27


def _pre_skip(data: bytes) -> int:
    idx = data.find(_HEAD_MAGIC, 0, 4096)
    if idx < 0 or idx + 12 > len(data):
        return 0
    return struct.unpack_from("<H", data, idx + 10)[0]


def ogg_duration_s(data: bytes) -> float | None:
    """Длительность в секундах или ``None``, если контейнер не читается.

    Ищем последнюю страницу с granule ≠ −1 (−1 значит «на этой странице
    пакет не заканчивается»), идём от конца назад — обычно хватает
    одной итерации.
    """
    if not data.startswith(_PAGE_MAGIC):
        return None
    pre_skip = _pre_skip(data)
    end = len(data)
    while True:
        idx = data.rfind(_PAGE_MAGIC, 0, end)
        if idx < 0:
            return None
        if idx + _MIN_PAGE_HEADER <= len(data) and data[idx + 4] == 0:
            granule = struct.unpack_from("<q", data, idx + 6)[0]
            if granule >= 0:
                samples = max(0, granule - pre_skip)
                return samples / OPUS_RATE
        end = idx
