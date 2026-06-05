#!/usr/bin/env python3
"""Export mysite content for platform KB ingest.

Sister script to ``apps/kb/management/commands/seed_kb_from_mysite``.
Runs on the mysite Django host (where the source Postgres lives),
emits a single JSON manifest the platform-side importer consumes.

### Why a separate script, not a platform-side Postgres reader

* Decouples platform code from mysite schema. mysite churns frequently
  (`services_app/models.py` has 25+ models); platform doesn't have to
  know about any of them past the manifest shape.
* No cross-DB credentials needed in platform env.
* Operator gets a checkpointable artefact — copy the JSON between hosts
  with scp / rsync / paste, no live DB link required.

### What it exports

* ``services``         — every Service with non-empty description
* ``masters``          — every Master with non-empty bio
* ``site_settings``    — the singleton SiteSettings row (contact info
                         + policy text where populated)
* ``promotions``       — every active Promotion

### Usage

On the mysite host::

    cd /home/taximeter/mysite/formula_tela_dev/mysite
    DJANGO_SETTINGS_MODULE=mysite.settings \\
        ../.venv312/bin/python tools/mysite_export_kb.py \\
        --output /tmp/mysite_kb_export.json

Then scp to the platform host and run::

    python manage.py seed_kb_from_mysite \\
        --tenant formula-tela \\
        --source /path/to/mysite_kb_export.json

This script intentionally lives in ``tools/`` rather than as a
mysite management command — mysite is not our repo, we don't add
files there. Operator places this in a temp dir on the mysite host
before running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON manifest.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        choices=["services", "masters", "site_settings", "promotions"],
        default=["services", "masters", "site_settings", "promotions"],
        help="Sections to export. Defaults to all.",
    )
    args = parser.parse_args()

    # Defer django import so --help works without DJANGO_SETTINGS_MODULE.
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        print(
            "ERROR: set DJANGO_SETTINGS_MODULE (e.g. DJANGO_SETTINGS_MODULE=mysite.settings) "
            "before running this script.",
            file=sys.stderr,
        )
        return 1

    import django

    django.setup()

    # Local imports — only valid after django.setup().
    from services_app.models import Master, Promotion, Service, SiteSettings

    manifest: dict[str, object] = {}

    if "services" in args.include:
        manifest["services"] = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "category": getattr(getattr(s, "category", None), "name", "") or "",
                "duration_minutes": getattr(s, "duration_minutes", None)
                or getattr(s, "duration", None),
                "price": float(s.price) if getattr(s, "price", None) is not None else None,
            }
            for s in Service.objects.exclude(description="").exclude(description__isnull=True)
        ]

    if "masters" in args.include:
        manifest["masters"] = [
            {"id": m.id, "name": m.name, "bio": m.bio}
            for m in Master.objects.exclude(bio="").exclude(bio__isnull=True)
        ]

    if "site_settings" in args.include:
        site = SiteSettings.objects.first()
        if site:
            manifest["site_settings"] = {
                "salon_name": site.salon_name or "",
                "phone": site.contact_phone or "",
                "email": site.contact_email or "",
                "address": site.address or "",
                "working_hours": site.working_hours or "",
                "description": site.description or "",
                "cancellation_policy": site.cancellation_policy or "",
                "privacy_policy": site.privacy_policy or "",
                "terms_of_service": site.terms_of_service or "",
            }

    if "promotions" in args.include:
        # Filter to "active" rows defensively — model may or may not have
        # is_active; use whatever exists.
        promo_qs = Promotion.objects.all()
        if hasattr(Promotion, "is_active"):
            promo_qs = promo_qs.filter(is_active=True)
        manifest["promotions"] = [
            {
                "id": p.id,
                "name": getattr(p, "name", "") or getattr(p, "title", ""),
                "description": getattr(p, "description", "") or getattr(p, "body", "") or "",
            }
            for p in promo_qs
        ]

    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = {k: len(v) if isinstance(v, list) else (1 if v else 0) for k, v in manifest.items()}
    print(f"Exported manifest → {output_path}")
    for section, count in counts.items():
        print(f"  {section}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
