"""Catalog sync services (Sprint 7 / C-track).

* :mod:`apps.catalog.services.http_client` — fetch from mysite/api/v1/catalog/* (C2 / DRF-573)
* ``apps.catalog.services.upserter`` — DTO → mirror upsert (C3 / DRF-574)
* ``apps.catalog.services.sync`` — orchestrator with Redis lock (C4 / DRF-575)
"""
