"""Celery app — skeleton.

Sprint 0 / A1 ships a placeholder. Wired up in Sprint 1 with broker URL,
acks_late + reject_on_worker_lost, and per-task time limits per
docs/architecture.md ADR-0004 + carry-over of mysite Celery hardening.
"""

from __future__ import annotations

import os

from celery import Celery  # type: ignore[import-untyped]
from dotenv import load_dotenv

# Autoload `.env` before Django reads settings — Celery workers also
# benefit when launched directly (`celery -A config worker ...`). No-op
# when the file is absent; real env vars take precedence.
load_dotenv(override=False)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("ai_bot_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
