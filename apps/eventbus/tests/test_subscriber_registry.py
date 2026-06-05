"""Pluggable subscriber registry — settings.DOMAIN_EVENT_SUBSCRIBERS.

PR-B refactor of the Phase 2.1 hard-coded ``_subscribers()`` returning
``[NoopSubscriber()]`` to a settings-resolved + cached list.
"""

from __future__ import annotations

import pytest

from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import (
    NoopSubscriber,
    Subscriber,
    _subscribers,
    reset_registry_cache,
)
from apps.eventbus.envelope import Envelope

pytestmark = pytest.mark.django_db(transaction=True)


# Module-level subscribers used by `settings.DOMAIN_EVENT_SUBSCRIBERS`
# overrides. Must be importable by dotted path — keep them module-level.
class _CountingSubscriber:
    """Records every envelope it sees in a class-level list."""

    seen: list[Envelope] = []

    def handle(self, envelope: Envelope) -> None:
        type(self).seen.append(envelope)


class _AlsoCountingSubscriber:
    seen: list[Envelope] = []

    def handle(self, envelope: Envelope) -> None:
        type(self).seen.append(envelope)


@pytest.fixture(autouse=True)
def _reset_registry_around_each_test():
    reset_registry_cache()
    _CountingSubscriber.seen.clear()
    _AlsoCountingSubscriber.seen.clear()
    yield
    reset_registry_cache()


def _emit_one():
    services.emit(
        V.BOOKING_CREATED,
        {
            "booking_id": "b1",
            "customer_id": "c",
            "service_id": "s",
            "slot_start": "2026-05-19T10:00:00Z",
            "booking_source": "ai_direct",
        },
        actor_type="system",
    )


class TestDefaultRegistry:
    def test_default_is_noop(self):
        # No settings override → registry = [NoopSubscriber].
        subs = _subscribers()
        assert len(subs) == 1
        assert isinstance(subs[0], NoopSubscriber)

    def test_satisfies_subscriber_protocol(self):
        subs = _subscribers()
        for s in subs:
            assert isinstance(s, Subscriber)


class TestSettingsOverride:
    def test_single_subscriber_via_settings(self, settings):
        settings.DOMAIN_EVENT_SUBSCRIBERS = [
            "apps.eventbus.tests.test_subscriber_registry._CountingSubscriber",
        ]
        reset_registry_cache()  # settings changed after cache primed in fixture

        _emit_one()
        dispatcher.dispatch_pending_events()

        # The counting subscriber should have seen the booking.created.
        booking_events = [e for e in _CountingSubscriber.seen if e.event_name == V.BOOKING_CREATED]
        assert len(booking_events) == 1

    def test_multiple_subscribers_all_invoked(self, settings):
        settings.DOMAIN_EVENT_SUBSCRIBERS = [
            "apps.eventbus.tests.test_subscriber_registry._CountingSubscriber",
            "apps.eventbus.tests.test_subscriber_registry._AlsoCountingSubscriber",
        ]
        reset_registry_cache()

        _emit_one()
        dispatcher.dispatch_pending_events()

        first = [e for e in _CountingSubscriber.seen if e.event_name == V.BOOKING_CREATED]
        second = [e for e in _AlsoCountingSubscriber.seen if e.event_name == V.BOOKING_CREATED]
        assert len(first) == 1
        assert len(second) == 1

    def test_one_subscriber_failing_does_not_block_others(self, settings):
        # Confirms Phase 2.1 isolation contract — one bad sub doesn't
        # silence good ones.

        class _BoomSubscriber:
            def handle(self, envelope: Envelope) -> None:
                raise RuntimeError("boom")

        # Module-level subscriber needed for dotted-path resolution.
        # We assign on the module by re-using _CountingSubscriber +
        # patching _subscribers directly here.
        from unittest.mock import patch

        with patch.object(
            dispatcher,
            "_subscribers",
            return_value=[_BoomSubscriber(), _CountingSubscriber()],
        ):
            _emit_one()
            counters = dispatcher.dispatch_pending_events()

        # Boom subscriber failed → counters.failed == 1, row not
        # dispatched. But the good subscriber DID see the envelope —
        # subscriber isolation is per-call, not per-row.
        assert counters["failed"] == 1
        assert counters["dispatched"] == 0
        good_seen = [e for e in _CountingSubscriber.seen if e.event_name == V.BOOKING_CREATED]
        assert len(good_seen) == 1


class TestCacheBehavior:
    def test_registry_cached_after_first_call(self):
        # First call resolves; second call returns same instances.
        a = _subscribers()
        b = _subscribers()
        # Same list object, same element identities — cache hit.
        assert a is b
        assert all(x is y for x, y in zip(a, b, strict=True))

    def test_explicit_reset_forces_re_resolution(self, settings):
        # Direct test of the reset helper (callable from non-Django
        # contexts like Celery worker boot). Hotfix C also wires the
        # Django setting_changed signal to call this automatically —
        # see TestSettingChangedAutoInvalidation below for the
        # implicit-reset path.
        a = _subscribers()
        # Bypass the setting_changed receiver by mutating the cache
        # directly and confirming explicit reset re-resolves.
        from apps.eventbus.dispatcher import _REGISTRY_CACHE  # noqa: F401

        reset_registry_cache()
        fresh = _subscribers()
        assert fresh is not a
        # Same default registry, but freshly resolved instance.
        assert isinstance(fresh[0], NoopSubscriber)


class TestResolve:
    def test_unknown_path_raises(self):
        from apps.eventbus.dispatcher import _resolve

        with pytest.raises((ImportError, AttributeError)):
            _resolve("apps.eventbus.no_such_module.NotASubscriber")


class TestSettingChangedAutoInvalidation:
    """Hotfix C (retro review #3): EventBusConfig.ready() wires the
    Django setting_changed signal to reset_registry_cache when the
    DOMAIN_EVENT_SUBSCRIBERS setting flips.

    Before this fix, tests using @override_settings(DOMAIN_EVENT_SUBSCRIBERS=...)
    would silently see the cached old registry — the docstring promised
    auto-invalidation but the receiver wasn't wired.
    """

    def test_settings_override_auto_invalidates_cache(self, settings):
        # Prime the cache with the default Noop registry.
        first = _subscribers()
        assert isinstance(first[0], NoopSubscriber)

        # Flip the setting — the receiver should clear the cache so the
        # NEXT _subscribers() call re-resolves to the new path.
        # NO manual reset_registry_cache() call.
        settings.DOMAIN_EVENT_SUBSCRIBERS = [
            "apps.eventbus.tests.test_subscriber_registry._CountingSubscriber",
        ]

        second = _subscribers()
        assert second is not first
        assert isinstance(second[0], _CountingSubscriber)

    def test_unrelated_setting_does_not_invalidate(self, settings):
        # Sanity: changing a different setting should NOT clear the cache.
        primed = _subscribers()
        settings.DEBUG = not settings.DEBUG
        after = _subscribers()
        assert after is primed  # same instance, cache intact
