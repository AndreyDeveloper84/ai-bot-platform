# Global KB tenant (KB-RAG Sub-2 / GH #115)

The `global_kb` tenant holds **shared, cross-salon knowledge-base content** —
the universal services catalog, the contraindication matrix, the aftercare
protocol, symptoms & consequences. Real salon tenants do not duplicate this
data; instead, the [KB retriever](../../apps/kb/services/retriever.py) issues
a second ChromaDB query against the `global_kb` collection for
`doc_type ∈ {SERVICE, CONTRAINDICATION, HELP_ARTICLE}` and merges results by
score.

This runbook covers **provisioning, ownership, and the do-not-delete
invariant**.

---

## Quick reference

| Field | Value |
| --- | --- |
| Slug | `global_kb` (see `apps/kb/constants.py::GLOBAL_KB_TENANT_SLUG`) |
| Name | `Global Knowledge Base` |
| `is_system` | `True` (admin delete refused, see PR #120) |
| `is_active` | `True` (deactivating it disables the global fallback for all salons) |
| Owner | Platform / KB-RAG track |
| Resolver | `apps.kb.services.global_tenant.get_global_kb_tenant()` |

---

## Provisioning (one-time per environment)

Run the management command on **each** environment — local dev, staging, and
production — exactly once. The command is idempotent: a second run is a no-op
that prints `already exists`.

```bash
python manage.py create_tenant \
    --slug global_kb \
    --name "Global Knowledge Base" \
    --system
```

The `--system` flag flips the row's `is_system` boolean to `True`. From that
point onward, **Django admin refuses to delete it** (`PermissionDenied`,
enforced in `TenantAdmin.delete_model` and `TenantAdmin.delete_queryset`). The
flag is read-only in the admin UI to prevent an accidental "uncheck and
delete" path.

> **Staging / prod**: run from a privileged shell on the deploy host (or
> `kubectl exec` / `docker compose exec`). Capture the printed UUID in the
> environment's provisioning log — it'll match across re-runs but is useful
> when correlating ChromaDB collection IDs.

---

## Why a system tenant (and not a `tenant_id IS NULL` row)?

Three alternatives were on the table; rationale lives in the parent issue
[#113](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/113). The
short version:

| | global_kb tenant ✅ | Per-tenant replication | Nullable tenant FK |
| --- | --- | --- | --- |
| Schema change | None | None | Migration; breaks middleware |
| Source of truth | One | N copies, drift guaranteed | One |
| Embedding cost | 1× | N× | 1× |
| Tenant isolation | Preserved (explicit whitelist) | Preserved | Holes everywhere (`WHERE tenant_id=X OR IS NULL`) |
| Edit propagation | Instant (one row update) | Re-sync N tenants | Instant |

`global_kb` keeps every KB-app contract (`Tenant.id` FK, `TenantScopedManager`,
per-tenant ChromaDB collection) intact while giving us a single editable
corpus.

---

## Do NOT delete

The retriever degrades gracefully when the row is missing — `get_global_kb_tenant()` returns
`None` and the WARN `kb.global_tenant.missing` fires once per process — but
**every salon loses access to ~150 KbDocument rows of seeded content** until
the row is re-provisioned and Sub-5's `seed_kb_from_gdocs` runs again
(~$0.05 OpenAI cost + Celery wait). That is a multi-hour recovery on a
production environment; treat the row as load-bearing.

If you absolutely must remove it (testing a fresh-environment path, for
example):

```python
# shell only — last resort
from apps.tenancy.models import Tenant
t = Tenant.all_objects.get(slug="global_kb")
t.is_system = False
t.save(update_fields=["is_system"])
t.delete()
```

Then immediately re-provision (`create_tenant ... --system`) and re-run the
seed (Sub-5). Do **not** leave the environment without a `global_kb` row.

---

## Re-activating a deactivated `global_kb`

If the row was deactivated by mistake (`is_active=False`), the retriever still
finds it (the helper uses `Tenant.all_objects`, not `Tenant.objects`) but
downstream consumers may filter on `is_active`. Re-enable explicitly:

```python
from apps.tenancy.models import Tenant
t = Tenant.all_objects.get(slug="global_kb")
t.is_active = True
t.save(update_fields=["is_active"])
```

---

## Verification

After provisioning, sanity-check from a shell:

```python
from apps.kb.services.global_tenant import get_global_kb_tenant
get_global_kb_tenant.cache_clear()  # in case the process cached None earlier
t = get_global_kb_tenant()
assert t is not None, "global_kb missing — re-run create_tenant"
assert t.slug == "global_kb"
print(f"OK — global_kb id={t.id} is_system={t.is_system}")
```

The cache (`functools.lru_cache(maxsize=1)`) means a long-running worker that
booted before the row existed will keep returning `None` until restarted (or
`cache_clear()` is called). On a freshly provisioned environment, **restart
the worker pool after running `create_tenant`** so retrieval sees the new
row.

---

## Related work

* **Sub-1 / PR #120** — `Tenant.is_system` field + admin delete guard
* **Sub-3 / PR #122** — `KbRetriever` global-fallback for shared doc_types
* **Sub-4 / PR #121** — Google Docs API client (this corpus's source)
* **Sub-5 (planned)** — `seed_kb_from_gdocs` management command — uses
  `get_global_kb_tenant()` to resolve the default target.
* **Sub-6 (planned)** — Seed run on 4 Google Docs + golden-query smoke tests.
* **Parent epic** — [#113](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/113)
