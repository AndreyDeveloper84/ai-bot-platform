"""Bootstrap Django ORM в standalone MCP-процессе.

Запускается ДО импорта tools/ — те читают модели services_app.

Subprocess родительского maxbot'а получает env с DJANGO_SETTINGS_MODULE
+ DB_* + OPENAI_API_KEY (см. ai_assistant.py при spawn'е через
StdioServerParameters(env=...)).
"""
from __future__ import annotations

import os


def setup_django() -> None:
    """Идемпотентный django.setup(). Запускать один раз при старте процесса."""
    import django
    from django.conf import settings

    if settings.configured and django.apps.apps.ready:
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    django.setup()
