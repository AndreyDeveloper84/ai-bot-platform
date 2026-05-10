"""Идемпотентный bootstrap Django ORM для standalone-процесса maxbot.

Цель: дать возможность handler'ам делать `from services_app.models import ...`
из отдельного процесса (не gunicorn) — отдельный systemd unit
formula-tela-maxbot.service.
"""
from __future__ import annotations

import os


def setup_django() -> None:
    """Вызвать django.setup() ровно один раз. Безопасно повторно."""
    import django
    from django.conf import settings

    if settings.configured and django.apps.apps.ready:
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    django.setup()
