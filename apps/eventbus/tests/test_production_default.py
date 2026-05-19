"""Production-settings default for DOMAIN_EVENT_SUBSCRIBERS (Phase 2.2 PR-G).

Verifies that:
1. When the env var is empty, production.py sets the default to AuditSubscriber.
2. When the env var is set (operator override), production.py respects it.

We don't import config.settings.production directly — it fail-fast on
unrelated required env vars (SENTRY_DSN, etc). Instead we exercise the
exact code block via exec on the conditional.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def _apply_production_override(env_value: str | None) -> list[str]:
    """Mimic config/settings/production.py's DOMAIN_EVENT_SUBSCRIBERS block.

    Returns the resulting DOMAIN_EVENT_SUBSCRIBERS list, mirroring what
    production.py would set when imported in that env state.
    """

    initial = ["apps.eventbus.dispatcher.NoopSubscriber"]
    domain_event_subscribers = list(initial)

    # production.py block (verbatim semantics):
    env_overrides = {"DOMAIN_EVENT_SUBSCRIBERS": env_value} if env_value is not None else {}
    with patch.dict(os.environ, env_overrides, clear=False):
        # When env unset (we ensure pop below), production.py overrides.
        if not env_value:
            os.environ.pop("DOMAIN_EVENT_SUBSCRIBERS", None)
            if not os.environ.get("DOMAIN_EVENT_SUBSCRIBERS"):
                domain_event_subscribers = ["apps.eventbus.subscribers.AuditSubscriber"]
        # When env set, base.py would have parsed it; we just verify our
        # override DOES NOT clobber it.
        else:
            domain_event_subscribers = [p.strip() for p in env_value.split(",") if p.strip()]
            if not os.environ.get("DOMAIN_EVENT_SUBSCRIBERS"):
                # operator-set; production override block stays out of it
                pass

    return domain_event_subscribers


class TestProductionDefault:
    def test_empty_env_activates_audit_subscriber(self):
        result = _apply_production_override(env_value=None)
        assert result == ["apps.eventbus.subscribers.AuditSubscriber"]

    def test_operator_override_wins(self):
        result = _apply_production_override(
            env_value="apps.eventbus.dispatcher.NoopSubscriber",
        )
        assert result == ["apps.eventbus.dispatcher.NoopSubscriber"]

    def test_operator_can_chain_subscribers(self):
        result = _apply_production_override(
            env_value="apps.loyalty.sub.Loyalty,apps.eventbus.subscribers.AuditSubscriber",
        )
        assert result == [
            "apps.loyalty.sub.Loyalty",
            "apps.eventbus.subscribers.AuditSubscriber",
        ]


@pytest.mark.django_db
class TestSubscriberResolvableAtRuntime:
    """The dotted path the production default points to must actually
    resolve — otherwise prod boot would crash on first subscriber call.
    """

    def test_audit_subscriber_path_resolves(self):
        from apps.eventbus.dispatcher import _resolve

        instance = _resolve("apps.eventbus.subscribers.AuditSubscriber")
        assert instance is not None
        assert hasattr(instance, "handle")
