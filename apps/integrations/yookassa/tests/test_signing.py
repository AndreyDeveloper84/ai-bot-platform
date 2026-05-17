"""HMAC signature verification tests (DRF-843 / Phase 1 / B7).

Covers every leaf in :mod:`apps.integrations.yookassa.signing`:

* Happy path — body + correct secret → matches.
* Tampered body — same signature header but mutated body → False.
* Missing ``sha256=`` prefix → False.
* Wrong digest length → False.
* Non-hex characters → False.
* Empty / missing header → False.
* Empty secret → raises ValueError (fail-closed posture).
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from apps.integrations.yookassa.signing import verify_webhook_signature


_SECRET = "test_secret_AB12_XYZ"  # pragma: allowlist secret
_BODY = b'{"event": "payment.succeeded", "object": {"id": "abc"}}'


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


class TestVerifyWebhookSignature:
    def test_happy_path(self) -> None:
        sig = _sign(_BODY, _SECRET)
        assert verify_webhook_signature(_BODY, _SECRET, sig) is True

    def test_tampered_body_rejected(self) -> None:
        # Compute the signature over the original body, then mutate.
        sig = _sign(_BODY, _SECRET)
        tampered = _BODY + b" "
        assert verify_webhook_signature(tampered, _SECRET, sig) is False

    def test_wrong_secret_rejected(self) -> None:
        sig = _sign(_BODY, "different_secret")
        assert verify_webhook_signature(_BODY, _SECRET, sig) is False

    def test_missing_prefix(self) -> None:
        digest = hmac.new(_SECRET.encode("utf-8"), _BODY, hashlib.sha256).hexdigest()
        # Bare hex with no "sha256=" prefix.
        assert verify_webhook_signature(_BODY, _SECRET, digest) is False

    def test_wrong_prefix(self) -> None:
        # Old-style sha1= header → reject (we only accept sha256).
        digest = hmac.new(_SECRET.encode("utf-8"), _BODY, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(_BODY, _SECRET, "sha1=" + digest) is False

    def test_short_digest(self) -> None:
        assert verify_webhook_signature(_BODY, _SECRET, "sha256=deadbeef") is False

    def test_non_hex_digest(self) -> None:
        # 64 chars but with non-hex 'z'.
        bad = "sha256=" + ("z" * 64)
        assert verify_webhook_signature(_BODY, _SECRET, bad) is False

    def test_empty_header(self) -> None:
        assert verify_webhook_signature(_BODY, _SECRET, "") is False

    def test_empty_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="YOOKASSA_SECRET_KEY"):
            verify_webhook_signature(_BODY, "", _sign(_BODY, _SECRET))

    def test_signature_is_case_insensitive_on_hex(self) -> None:
        """compare_digest lowercases the sent hex before compare."""
        digest = hmac.new(_SECRET.encode("utf-8"), _BODY, hashlib.sha256).hexdigest()
        upper = "sha256=" + digest.upper()
        assert verify_webhook_signature(_BODY, _SECRET, upper) is True
