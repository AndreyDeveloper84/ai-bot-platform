"""HMAC-SHA256 + timestamp anti-replay verification for the events ingest.

`event-contract.md` §6.2 prescribes:

  - Shared secret stored in vault, env-exposed as
    ``settings.EVENT_INGEST_HMAC_SECRET``. Quarterly rotation.
  - ``X-Ayla-Event-Signature: sha256=<hex>`` with
    ``hex = hmac_sha256(secret, raw_request_body)``.
  - ``X-Ayla-Event-Timestamp: <unix_ms>`` — reject if
    ``|now - timestamp| > 300s`` (anti-replay).
  - Constant-time compare to avoid timing oracles.
  - On any failure: caller returns HTTP 401 (§8.3), increments
    ``events_signature_failed_total{reason}``, logs the headers (NOT
    the body — body may carry partial valid data).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Final


logger = logging.getLogger(__name__)


# `event-contract.md` §6.2 — anti-replay window (±300s around server `now`).
TIMESTAMP_WINDOW_S: Final[float] = 300.0

# Canonical Django META keys for the two headers. Django prefixes
# request headers with ``HTTP_`` and uppercases / underscore-substitutes
# the hyphens.
_SIG_HEADER_META: Final[str] = "HTTP_X_AYLA_EVENT_SIGNATURE"
_TS_HEADER_META: Final[str] = "HTTP_X_AYLA_EVENT_TIMESTAMP"


# Failure reason slugs — match the §8.3 Prometheus counter labels.
REASON_OK: Final[str] = "ok"
REASON_NO_SECRET: Final[str] = "no_secret"
REASON_MISSING_SIGNATURE: Final[str] = "missing_signature"
REASON_MISSING_TIMESTAMP: Final[str] = "missing_timestamp"
REASON_INVALID_TIMESTAMP: Final[str] = "invalid_timestamp"
REASON_TIMESTAMP_STALE: Final[str] = "timestamp_stale"
REASON_MALFORMED_SIGNATURE: Final[str] = "malformed_signature"
REASON_HMAC_MISMATCH: Final[str] = "hmac_mismatch"


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of an HMAC + timestamp check.

    Fields:

    * ``ok``     — True iff the request signature AND timestamp pass.
    * ``reason`` — Slug from the ``REASON_*`` constants. ``REASON_OK`` on success.
    """

    ok: bool
    reason: str


def verify_signature(
    *,
    body: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
    now: float | None = None,
) -> VerificationResult:
    """Validate the HMAC + timestamp pair.

    Args:
      body: Raw request body (bytes — encoded form). The HMAC MUST be
            computed over the exact bytes received; any normalisation
            (whitespace, key order) would break verification.
      signature_header: Value of ``X-Ayla-Event-Signature``. Expected
            shape ``sha256=<hex>``; any other shape is malformed.
      timestamp_header: Value of ``X-Ayla-Event-Timestamp``. Unix ms.
      secret: Shared secret. Empty secret → ``REASON_NO_SECRET``
            (misconfigured deploy — fail loudly rather than silently
            accept).
      now: Override for ``time.time()`` — used by tests to simulate
            clock drift. Production callers leave this None.

    Returns:
      :class:`VerificationResult` with ``ok`` + ``reason``.
    """
    if not secret:
        # Misconfigured deploy. We refuse to accept anything rather
        # than silently fall through — silent accept is the worst-of-
        # both worlds (the operator thinks HMAC is on; nothing checks).
        logger.warning("eventbus.ingest.no_secret")
        return VerificationResult(ok=False, reason=REASON_NO_SECRET)

    if not signature_header:
        return VerificationResult(ok=False, reason=REASON_MISSING_SIGNATURE)
    if not timestamp_header:
        return VerificationResult(ok=False, reason=REASON_MISSING_TIMESTAMP)

    # Parse the signature header. The contract is sha256=<hex>; any
    # other prefix is malformed.
    if not signature_header.startswith("sha256="):
        return VerificationResult(ok=False, reason=REASON_MALFORMED_SIGNATURE)
    expected_hex = signature_header[len("sha256=") :].strip()
    if not expected_hex:
        return VerificationResult(ok=False, reason=REASON_MALFORMED_SIGNATURE)

    # Parse timestamp. Contract says unix milliseconds; tolerate
    # str/int interchangeably.
    try:
        ts_ms = int(timestamp_header)
    except (TypeError, ValueError):
        return VerificationResult(ok=False, reason=REASON_INVALID_TIMESTAMP)

    server_now = time.time() if now is None else now
    skew_s = abs(server_now - (ts_ms / 1000.0))
    if skew_s > TIMESTAMP_WINDOW_S:
        return VerificationResult(ok=False, reason=REASON_TIMESTAMP_STALE)

    # Compute expected HMAC + constant-time compare.
    computed_hex = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed_hex, expected_hex):
        return VerificationResult(ok=False, reason=REASON_HMAC_MISMATCH)

    return VerificationResult(ok=True, reason=REASON_OK)


def signature_header_from(request: object) -> str:
    """Pull the canonical signature header from a Django request.

    Helper for the view layer; centralises the ``request.META`` key.
    """
    meta = getattr(request, "META", {}) or {}
    return str(meta.get(_SIG_HEADER_META) or "")


def timestamp_header_from(request: object) -> str:
    """Pull the canonical timestamp header from a Django request."""
    meta = getattr(request, "META", {}) or {}
    return str(meta.get(_TS_HEADER_META) or "")
