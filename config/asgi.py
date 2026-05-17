"""ASGI config for ai-bot-platform."""

import os

from dotenv import load_dotenv

# Autoload `.env` before Django reads settings. No-op if absent; real
# env vars override `.env` (production uses platform secrets, `.env` is
# dev-only).
load_dotenv(override=False)

from django.core.asgi import get_asgi_application  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
