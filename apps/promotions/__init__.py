"""Promotions app — promo codes + discount validation (Phase 1 / B6 / DRF-842).

Separated from :mod:`apps.catalog` because admins manage promotions on a
different cadence than the catalog mirror (the catalog is read-only,
auto-synced from mysite every 15 minutes; promotions are operator-edited
through Django admin and are platform-owned).

Hosts the :class:`apps.promotions.models.Promotion` model + the
:func:`apps.promotions.services.validate_promo` validation entry point
used by the ``calc_price`` LLM tool (see
:mod:`apps.skills.booking.tools`).
"""
