"""HMAC-SHA256 verification of YooKassa webhook signatures.

### Format

Per YooKassa's docs, the signature header is ``sha256=<hex>``. The
hex digest is computed over the raw request body (bytes, not the
parsed JSON) using the shop's secret key as the HMAC key.

We strictly enforce the ``sha256=`` prefix to avoid silently accepting
unprefixed values that a misconfigured upstream might send. ``False``
is returned on:

* Missing / empty prefix.
* Wrong prefix (e.g. ``sha1=`` from an old generator).
* Hex digest of unexpected length / non-hex characters (catches
  truncation / typos before ``compare_digest`` is even reached).
* Mismatched digest (the main case — wrong secret or tampered body).

### Empty-secret handling

An empty secret is fatal: a webhook arriving at a shop with no secret
configured is a misconfiguration that MUST NOT pass verification.
``ValueError`` is raised so the receiver short-circuits to "bad
signature" and audits the misconfig (vs. silently accepting forged
deliveries). This mirrors the catalog webhook's fail-closed posture.

### Why constant-time

``hmac.compare_digest`` masks timing differences that would otherwise
leak the byte-position of the first mismatched character to an
attacker probing with carefully-crafted signatures. The reference
implementation in :mod:`apps.catalog.webhooks.views` uses the same
helper for the same reason.
"""

from __future__ import annotations

import hmac
from hashlib import sha256


_SIGNATURE_PREFIX = "sha256="
# A SHA-256 hex digest is exactly 64 lowercase hex characters.
_EXPECTED_HEX_LENGTH = 64


def verify_webhook_signature(
    body: bytes,
    secret: str,
    header_value: str,
) -> bool:
    """Verify the HMAC-SHA256 signature of a YooKassa webhook delivery.

    Args:
      body: The RAW request body as bytes — NOT the parsed JSON dict.
            Re-encoding via ``json.dumps`` would produce a different
            byte stream and the signature would never match.
      secret: ``settings.YOOKASSA_SECRET_KEY``. Empty raises
              :class:`ValueError` — see module docstring.
      header_value: The contents of the YooKassa signature HTTP header.
              Expected format: ``sha256=<64 lowercase hex chars>``.

    Returns:
      True iff the signature matches a freshly computed HMAC of the
      body with ``secret`` as the key. False on any of: missing
      prefix, wrong-length digest, non-hex chars, or mismatched
      digest.

    Raises:
      ValueError: ``secret`` is empty / falsy. Fail-closed; the
        receiver must audit + reject (audit + 200) rather than
        silently accept.
    """
    if not secret:
        # Fail-closed at the API boundary — better than letting a
        # misconfigured deploy silently accept forged webhooks.
        raise ValueError("YOOKASSA_SECRET_KEY is empty — cannot verify signature")
    if not header_value:
        return False
    if not header_value.startswith(_SIGNATURE_PREFIX):
        return False

    sent_hex = header_value[len(_SIGNATURE_PREFIX) :]
    if len(sent_hex) != _EXPECTED_HEX_LENGTH:
        return False
    # Reject non-hex characters cheaply before HMAC computation.
    try:
        int(sent_hex, 16)
    except ValueError:
        return False

    expected = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return hmac.compare_digest(sent_hex.lower(), expected)
