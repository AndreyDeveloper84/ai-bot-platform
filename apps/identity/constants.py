"""Identity app constants.

Well-known identifiers that several identity services share. Kept in a
dedicated module (mirrors :mod:`apps.kb.constants`) so the slug isn't
duplicated across the resolver, the sentinel-tenant helper, the seed
data migration, and tests.
"""

from __future__ import annotations

# Slug of the system Tenant that OWNS the global (tenant-less) bot identity
# (#1019 / EPIC #1014). One nationwide bot serves every salon; the
# conversation runs at ``current_tenant()=None`` (discovery) and a tenant is
# selected only at booking. But ``BotUser.unique_together (tenant, channel,
# channel_user_id)`` requires a non-null tenant FK — so the global BotUser is
# parked under this sentinel system tenant. It is NOT a real salon and carries
# no commercial state; it exists purely to satisfy the natural key without a
# schema migration. Distinct from ``global_kb`` (KB corpus) on purpose —
# identity and KB have independent lifecycles.
GLOBAL_BOT_TENANT_SLUG = "global_bot"
