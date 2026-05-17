#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

from dotenv import load_dotenv

# Autoload `.env` from the project root before Django imports settings.
# No-op when the file is absent (production typically uses real env vars
# from the deploy platform's secret store; `.env` is dev-only).
# ``override=False`` keeps real env vars winning over `.env` so an
# operator can still shell-override individual values for one-off runs.
load_dotenv(override=False)


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH? Did you forget to activate a "
            "virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
