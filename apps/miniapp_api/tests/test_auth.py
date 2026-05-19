"""Unit tests for initData HMAC verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest

from apps.miniapp_api.auth import (
    InitDataBadSignature,
    InitDataMalformed,
    InitDataNotConfigured,
    InitDataStale,
    extract_init_data,
    verify_init_data,
)


BOT_TOKEN = "test-bot-token-xyz"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    """Build a valid signed initData query string with the given params + token."""

    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _ts_now() -> int:
    return int(time_module.time())


class TestExtractInitData:
    def test_missing_header(self) -> None:
        with pytest.raises(InitDataMalformed):
            extract_init_data(None)

    def test_wrong_prefix(self) -> None:
        with pytest.raises(InitDataMalformed):
            extract_init_data("Bearer abc123")

    def test_correct_prefix(self) -> None:
        assert extract_init_data("MaxInitData raw-data-here") == "raw-data-here"


class TestVerifyInitData:
    def test_happy_path(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 12345, "first_name": "Мария"}),
            "auth_date": str(ts),
        }
        raw = _sign(params)
        verified = verify_init_data(raw, bot_token=BOT_TOKEN, now=ts)
        assert verified.user_id == "12345"
        assert verified.user["first_name"] == "Мария"
        assert verified.auth_date == ts

    def test_bad_signature(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 1}),
            "auth_date": str(ts),
        }
        raw = _sign(params, token="wrong-token")
        with pytest.raises(InitDataBadSignature):
            verify_init_data(raw, bot_token=BOT_TOKEN, now=ts)

    def test_tampered_user_id(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 1}),
            "auth_date": str(ts),
        }
        raw = _sign(params)
        # Swap the user payload for a different id without re-signing.
        tampered = raw.replace(
            urlencode({"user": params["user"]})[5:],
            urlencode({"user": json.dumps({"id": 999})})[5:],
        )
        with pytest.raises(InitDataBadSignature):
            verify_init_data(tampered, bot_token=BOT_TOKEN, now=ts)

    def test_stale_auth_date(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 1}),
            "auth_date": str(ts - 3601),  # > 60 min
        }
        raw = _sign(params)
        with pytest.raises(InitDataStale):
            verify_init_data(raw, bot_token=BOT_TOKEN, now=ts)

    def test_not_configured(self) -> None:
        with pytest.raises(InitDataNotConfigured):
            verify_init_data("user=%7B%7D", bot_token="", now=_ts_now())

    def test_empty_init_data(self) -> None:
        with pytest.raises(InitDataMalformed):
            verify_init_data("", bot_token=BOT_TOKEN, now=_ts_now())

    def test_missing_hash(self) -> None:
        with pytest.raises(InitDataMalformed):
            verify_init_data("auth_date=1&user=%7B%7D", bot_token=BOT_TOKEN, now=_ts_now())

    def test_duplicate_key_rejected(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 1}),
            "auth_date": str(ts),
        }
        raw = _sign(params)
        # Inject a second hash key in front — duplicate keys must be rejected.
        tampered = "hash=00&" + raw
        with pytest.raises(InitDataMalformed):
            verify_init_data(tampered, bot_token=BOT_TOKEN, now=ts)

    def test_missing_user(self) -> None:
        ts = _ts_now()
        raw = _sign({"auth_date": str(ts)})
        with pytest.raises(InitDataMalformed):
            verify_init_data(raw, bot_token=BOT_TOKEN, now=ts)

    def test_chat_payload_parsed(self) -> None:
        ts = _ts_now()
        params = {
            "user": json.dumps({"id": 1}),
            "chat": json.dumps({"id": 99, "type": "private"}),
            "auth_date": str(ts),
        }
        raw = _sign(params)
        verified = verify_init_data(raw, bot_token=BOT_TOKEN, now=ts)
        assert verified.chat is not None and verified.chat["id"] == 99
