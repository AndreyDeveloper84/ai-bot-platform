"""Public constants for the KB app (KB-RAG Sub-2 / GH #115).

Constants live here (not inside service modules) so that consumers —
retriever (Sub-3), seed command (Sub-5), management runbooks, and
future skills — agree on the same slug without importing each other.

The slug is the contract; ``Tenant.is_system`` (Sub-1) is admin safety,
not the lookup key. See ``docs/operations/global-kb-tenant.md`` for the
full rationale.
"""

from __future__ import annotations

# Slug of the system tenant that owns shared cross-salon KB content
# (the universal services catalog, the contraindication matrix, the
# aftercare protocol, etc.). Provisioned via:
#
#     python manage.py create_tenant \
#         --slug global_kb \
#         --name "Global Knowledge Base" \
#         --system
GLOBAL_KB_TENANT_SLUG = "global_kb"
