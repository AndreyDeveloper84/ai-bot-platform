"""Fixtures for the channel tests.

Two fixtures, both autouse, and the reason they are autouse is the defect each
closes: tests that reached a vendor over the network without anybody having
asked them to (DRF-2691 for the model, DRF-2696 for MAX).

### What was happening

``GlobalMaxHandler`` runs the intent resolver after a free-text concierge turn
(``resolve_and_log_turn_intent``, DRF-1273). The resolver is on by default
(``INTENT_RESOLUTION_LIVE_ENABLED``) and builds its own ``RouterLLMClient``, so
a test that stubs the concierge reply and types a phrase still makes a second
model call — a real one: ``POST https://api.openai.com/v1/chat/completions``
with the CI's placeholder key.

Measured on `dev` 47c22f9d, the shard-5 selection on Postgres, ``-n 4``: 91
tests in 23 files of this directory reached the vendor. The usual answer is a
401, which the resolver swallows (it never raises) — which is why nobody saw it.
When the connection fails instead, the retry layer writes an audit row from a
worker thread, on that thread's own connection; the row commits outside the
test's transaction and survives its rollback. Two such rows are what
``TestRetryAnthropic::test_audit_row_per_failed_attempt`` counted on CI
(``assert 3 == 1``, run 36827421855).

So a test here passed or failed by the weather between the runner and a vendor,
and what it left behind broke a test in another directory.

With this fixture, same selection of ``apps/channels``, same four workers: 1481
tests, none red, and no committed audit row after any of them (105 before).

### Why a directory-wide fixture and not one more copy

Four files here already carried this stub under the same name
(``_no_intent_llm``) with the same explanation — the rule had been reached four
times, one file at a time, and the other 23 files were found by measurement,
not by review. A fifth copy would have fixed the files that were measured and
left the next one to be found the same way.

### What it does not do

* It does not touch tests that give the handler their own resolver: a later
  ``monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", ...)``
  replaces this one for that test.
* It covers this directory only (not ``apps/channels/max/tests``, which left
  no rows in the same measurement). Whether tests elsewhere reach a vendor
  was measured for the shard-5 selection (they do not leave retry rows) and for
  nothing else.
* The resolver's own behaviour is tested where it lives,
  ``apps/orchestrator/tests`` — not through this handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max import outbound as max_outbound


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    """The post-reply intent resolver does not run: no second model call."""
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


@pytest.fixture(autouse=True)
def _no_chat_indicator(monkeypatch):
    """«Прочитано» and «печатает…» are not sent: no call to MAX.

    DRF-2696. The handler fires ``send_chat_action`` at the start of a turn,
    and the function goes to the network whenever a bot token is set — which
    in a test run it is, as soon as any earlier test in the worker has put
    one in place. Measured on `dev` f8579a5e: 10 tests in three files of this
    directory made 22 real ``POST botapi.max.ru/chats/<id>/actions``. The
    indicator swallows its own failures, so the tests were green either way.

    41 test files in the repository already stub this function one at a
    time; this is the same stub for the directory. A test that asserts on
    the indicator installs its own spy, which replaces this one.
    """
    monkeypatch.setattr(max_outbound, "send_chat_action", lambda **kwargs: None)
