"""Identity services package.

Sprint 2 / A2 shipped `resolve_or_create_bot_user` + `delete_bot_user_data`
in a flat module ``apps.identity.services``. Sprint 6 / P-track adds
RFM/LTV/churn/tier services. Converting to a package gives each its own
file without ballooning resolver.py.

### Backward compat

Existing call sites (`from apps.identity.services import resolve_or_create_bot_user`)
keep working — re-exports from :mod:`apps.identity.services.resolver`.

### New Sprint 6 services (P2-P6)

- :mod:`apps.identity.services.rfm` — `compute_rfm` (P2 / DRF-528)
- :mod:`apps.identity.services.ltv` — `compute_ltv` (P3 / DRF-529)
- :mod:`apps.identity.services.churn` — `compute_churn_risk` (P4 / DRF-530)
- :mod:`apps.identity.services.tier` — `compute_tier` (P5 / DRF-531)
- :mod:`apps.identity.services.recompute` — `recompute_profile` (P6 / DRF-532)
"""

from apps.identity.services.ayla_link import ensure_ayla_link
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.identity.services.resolver import (
    delete_bot_user_data,
    resolve_or_create_bot_user,
    resolve_or_create_global_bot_user,
)

__all__ = [
    "delete_bot_user_data",
    "ensure_ayla_link",
    "get_global_bot_tenant",
    "resolve_or_create_bot_user",
    "resolve_or_create_global_bot_user",
]
