"""LLM test conftest — re-enables `PII_TOKENIZER_ENABLED` for this dir.

`config/settings/local.py` defaults `PII_TOKENIZER_ENABLED=False` so the
broader pytest suite doesn't try to reach a non-existent Redis. PII-
specific tests in this directory exercise the tokenizer end-to-end
с monkey-patched `_redis_client` (see `fake_redis` fixture) — those
need the decorator's tokenize/detokenize path active, so we flip the
flag back ON for THIS directory only.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_pii_tokenizer(settings: pytest.FixtureRequest) -> None:
    settings.PII_TOKENIZER_ENABLED = True  # type: ignore[attr-defined]
