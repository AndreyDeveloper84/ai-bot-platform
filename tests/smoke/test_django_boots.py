"""Sprint 0 / A1 smoke test: 'Django boots'.

Boundary contract for Sprint 0:
- Django project loads with all 20 apps in INSTALLED_APPS
- `django.setup()` succeeds
- `manage.py check --deploy` is allowed to warn but not error
- Each platform app is importable via app registry

Heavier integration smokes (DB connectivity, Celery roundtrip, channels
ingress) land sprint-by-sprint per docs/architecture.md.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.core.management import call_command


PLATFORM_APPS = [
    "tenancy",
    "identity",
    "conversations",
    "orchestrator",
    "skills",
    "tools",
    "kb",
    "channels",
    "ingress",
    "workers",
    "consent",
    "audit",
    "events",
    "experiments",
    "voice",
    "catalog",
    "replay",
    "promptreg",
    "adminconsole",
    "handoff",
]


def test_django_boots():
    """Django app registry initialised — no ImportError, no AppConfig misnames."""
    assert apps.ready, "Django app registry is not ready"


@pytest.mark.parametrize("label", PLATFORM_APPS)
def test_platform_app_registered(label: str):
    """Every platform app from docs/architecture.md is in INSTALLED_APPS."""
    config = apps.get_app_config(label)
    assert config.name == f"apps.{label}"


def test_manage_check_passes():
    """`manage.py check` exits cleanly. Failures = blocking config bugs."""
    call_command("check")
