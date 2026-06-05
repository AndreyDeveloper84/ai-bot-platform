"""Minimal ULID generator — Crockford base32, time-sortable.

event-taxonomy.md §2 requires ``event_id`` to be a ULID. ULID
guarantees lexicographic sort order matches creation order — the
property the outbox poller relies on for FIFO dispatch.

Inlined here to avoid a new runtime dependency. The format is the
canonical ULID spec (https://github.com/ulid/spec):

    01ARZ3NDEKTSV4RRFFQ69G5FAV
    |----------|--------------|
       time          random
        48b           80b
        10 chars      16 chars

Encoded as 26 chars of Crockford base32 (uppercase, no I/L/O/U).
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # pragma: allowlist secret


def new_ulid() -> str:
    """Return a fresh 26-char ULID.

    Time component = current ms since epoch, big-endian 48 bits.
    Random component = 80 random bits from os.urandom.
    """

    ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ts_ms, 10) + _encode(rand, 16)


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def is_valid(s: str) -> bool:
    """Cheap shape check — 26 chars, all in the Crockford alphabet."""

    if len(s) != 26:
        return False
    return all(c in _CROCKFORD for c in s)
