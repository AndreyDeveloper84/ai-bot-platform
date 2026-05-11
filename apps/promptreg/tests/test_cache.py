"""Cache tests (DRF-480 / Sprint 4 / A5)."""

from __future__ import annotations

import json
import uuid

import pytest

from apps.promptreg import cache as cache_mod

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_caches():
    cache_mod.invalidate_all()
    yield
    cache_mod.invalidate_all()


@pytest.fixture(autouse=True)
def _disable_subscriber(settings):
    """Don't start the background thread in unit tests — use the sync hook."""

    settings.PROMPTREG_DISABLE_SUBSCRIBER = True


class _Dummy:
    """Stand-in for a PromptVersion / ThresholdConfig / DisclaimerLibrary."""

    def __init__(self, label: str) -> None:
        self.label = label


class TestPutGet:
    def test_prompt_hit_miss(self):
        t = uuid.uuid4()
        assert cache_mod.get_prompt_cached(t, "faq") is None
        v = _Dummy("p1")
        cache_mod.put_prompt(t, "faq", v)
        assert cache_mod.get_prompt_cached(t, "faq") is v

    def test_threshold_hit_miss(self):
        t = uuid.uuid4()
        assert cache_mod.get_threshold_cached(t, "k", "") is None
        v = _Dummy("t1")
        cache_mod.put_threshold(t, "k", "", v)
        assert cache_mod.get_threshold_cached(t, "k", "") is v

    def test_disclaimer_hit_miss(self):
        t = uuid.uuid4()
        assert cache_mod.get_disclaimer_cached(t, "medical", "high") is None
        v = _Dummy("d1")
        cache_mod.put_disclaimer(t, "medical", "high", v)
        assert cache_mod.get_disclaimer_cached(t, "medical", "high") is v


class TestInvalidate:
    def test_surgical_eviction(self):
        t = uuid.uuid4()
        cache_mod.put_prompt(t, "faq", _Dummy("a"))
        cache_mod.put_prompt(t, "handoff", _Dummy("b"))
        cache_mod.invalidate(t, "prompt", ("faq",))
        assert cache_mod.get_prompt_cached(t, "faq") is None
        # Other key untouched.
        assert cache_mod.get_prompt_cached(t, "handoff") is not None

    def test_tenant_wide_eviction(self):
        t = uuid.uuid4()
        other = uuid.uuid4()
        cache_mod.put_prompt(t, "faq", _Dummy("a"))
        cache_mod.put_prompt(t, "handoff", _Dummy("b"))
        cache_mod.put_prompt(other, "faq", _Dummy("c"))
        cache_mod.invalidate(t, "prompt", None)
        # Tenant t fully evicted.
        assert cache_mod.get_prompt_cached(t, "faq") is None
        assert cache_mod.get_prompt_cached(t, "handoff") is None
        # Other tenant unaffected.
        assert cache_mod.get_prompt_cached(other, "faq") is not None

    def test_unknown_kind_logs_but_no_crash(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            cache_mod.invalidate(uuid.uuid4(), "bogus", None)
        assert any("unknown_kind" in r.message for r in caplog.records)


class TestSyncDispatchHook:
    def test_dispatch_invalidate_sync_evicts(self):
        t = uuid.uuid4()
        cache_mod.put_prompt(t, "faq", _Dummy("x"))
        cache_mod._dispatch_invalidate_sync(t, "prompt", ("faq",))
        assert cache_mod.get_prompt_cached(t, "faq") is None


class TestMessageHandler:
    def test_handle_message_prompt_eviction(self):
        t = uuid.uuid4()
        cache_mod.put_prompt(t, "faq", _Dummy("x"))
        msg = {
            "type": "pmessage",
            "channel": f"{cache_mod._CHANNEL_PREFIX}{t}",
            "data": json.dumps({"kind": "prompt", "key": ["faq"]}),
        }
        cache_mod._handle_message(msg)
        assert cache_mod.get_prompt_cached(t, "faq") is None

    def test_handle_message_tenant_wide(self):
        t = uuid.uuid4()
        cache_mod.put_prompt(t, "faq", _Dummy("x"))
        cache_mod.put_prompt(t, "handoff", _Dummy("y"))
        msg = {
            "type": "pmessage",
            "channel": f"{cache_mod._CHANNEL_PREFIX}{t}",
            "data": json.dumps({"kind": "prompt"}),  # no key
        }
        cache_mod._handle_message(msg)
        assert cache_mod.get_prompt_cached(t, "faq") is None
        assert cache_mod.get_prompt_cached(t, "handoff") is None

    def test_bad_tenant_channel_ignored(self, caplog):
        import logging

        msg = {
            "type": "pmessage",
            "channel": f"{cache_mod._CHANNEL_PREFIX}not-a-uuid",
            "data": "{}",
        }
        with caplog.at_level(logging.WARNING):
            cache_mod._handle_message(msg)
        assert any("bad_tenant" in r.message for r in caplog.records)

    def test_bad_json_payload_ignored(self, caplog):
        import logging

        t = uuid.uuid4()
        msg = {
            "type": "pmessage",
            "channel": f"{cache_mod._CHANNEL_PREFIX}{t}",
            "data": "this is not json",
        }
        with caplog.at_level(logging.WARNING):
            cache_mod._handle_message(msg)
        assert any("bad_payload" in r.message for r in caplog.records)


class TestPublishSwallowsRedisError:
    def test_publish_does_not_raise_on_redis_down(self, monkeypatch, caplog):
        import logging

        class _Boom:
            def publish(self, *a, **kw):
                import redis as r

                raise r.ConnectionError("redis down")

        monkeypatch.setattr(cache_mod, "_client", lambda: _Boom())

        with caplog.at_level(logging.ERROR):
            cache_mod.publish_invalidate(uuid.uuid4(), "prompt", ("faq",))
        assert any("publish_failed" in r.message for r in caplog.records)
