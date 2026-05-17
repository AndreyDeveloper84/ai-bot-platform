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

## Seeding content (KB-RAG Sub-5 / GH #118)

After the `global_kb` tenant exists, populate it with content by reading
Google Docs via `seed_kb_from_gdocs`. The command is idempotent: it
diffs by SHA-256 checksum per heading-derived `source_uri` and only
writes a new `KbDocument` version when content actually changed. Re-runs
on unchanged content are zero-touch (no row update, no version bump, no
re-embed).

### Per-source granularity matrix

| Source Doc | `--doc-type` | `--granularity` | Why |
| --- | --- | --- | --- |
| Universal services catalog | `service` | `subsection` | Each H3 = one service variant; retrieval matches on the service name. |
| Contraindication matrix | `contraindication` | `subsection` | Each H3 = one indication (condition / medication / state). |
| Aftercare protocol | `help_article` | `section` | H2 = one body zone / procedure; sub-points stay in context. |
| Symptoms & consequences | `help_article` | `subsection` | H3 = one symptom; H2 = body system grouping. |

### First run

```bash
python manage.py seed_kb_from_gdocs \
    --doc-id 1abc...XYZ \
    --doc-type service \
    --granularity subsection
```

Defaults to `--tenant-slug global_kb` (via
`apps.kb.services.global_tenant.get_global_kb_tenant`). Pass
`--tenant-slug <other>` for non-global content (tenant-specific FAQ
imported from a private doc, for example). Add `--dry-run` to preview
`[CREATE]` / `[UPDATE]` / `[SKIP]` lines without touching the DB.

### Re-ingest after editing a Doc

Re-run the same command. The chunker re-splits the doc, hashes each
chunk, and:

* **Unchanged chunks** → `[SKIP]` (zero DB writes).
* **Edited chunks** → new row at `version = previous + 1`,
  `embedded_at = NULL`. The old version is **preserved** for replay
  forensics.
* **New chunks** (a heading you just added) → `[CREATE]` at version=1.
* **Removed chunks** (a heading you deleted) → orphaned. The command
  doesn't auto-delete; the K6 ingester still re-embeds the latest
  version, and the retriever ranks by relevance — orphans naturally
  sink in score. If you need them gone, delete the row from the admin.

The K6 Celery beat (`apps.kb.tasks.embed_pending_documents`) picks up
the new `embedded_at IS NULL` rows on its next pass — typically within
a few minutes. Restarting workers is **not** required; the row is
re-embedded by the same long-running task that handles tenant-side
catalog edits.

### Failure modes the command surfaces as `CommandError`

* `global_kb tenant not provisioned` — run `create_tenant --system` first.
* `tenant slug not found: 'xyz'` — typo or wrong environment.
* `GoogleDocsClient: credentials file not found at ...` — set
  `GOOGLE_DOCS_SERVICE_ACCOUNT_FILE` to the mounted SA key path
  (see `google-docs-credentials.md`).
* `Access denied to Google Doc '<id>'` — share the doc with the SA
  email as Viewer.

The command never partially seeds: a mid-doc Google API failure
rolls the whole transaction back, so a retry starts from a clean
slate.

### Verifying the seed (smoke tests)

After seeding (and waiting for `embed_pending_kb_documents` to finish),
run the golden-query smoke suite to confirm the corpus is queryable
end-to-end through the live retriever:

```bash
./.venv/Scripts/python.exe -m pytest \
    apps/kb/services/tests/test_global_fallback_smoke.py -m smoke -v
```

The suite is **opt-in** — `pyproject.toml`'s default `addopts` pin
`-m "not smoke"` so a plain `pytest` run never touches OpenAI. The
five tests live in `apps/kb/services/tests/test_global_fallback_smoke.py`
and cover the high-value end-user intents from issue
[#119](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/119):

| # | Intent | `doc_types` filter |
| --- | --- | --- |
| 1 | "I want to relax — which massage?" | `["service"]` |
| 2 | "I'm pregnant — can I do laser?" | `["contraindication"]` |
| 3 | "What should I do after biorevitalization?" | `["help_article"]` |
| 4 | "Redness after procedure — is it normal?" | `["contraindication", "help_article"]` |
| 5 | "My back hurts — what do you suggest?" | `["service", "contraindication"]` |

Each test creates a fresh non-`global_kb` tenant (so the tenant's own
collection is empty), runs `search_kb` with a real
`OpenAIProvider`, and asserts:

* at least one hit returned;
* the top hit carries `metadata.kb_source == "global"` (proving the
  fallback fired);
* a lenient substring matches the expected topic in section title /
  source_uri / chunk text.

**When to run**: after a fresh seed on a new environment, before
declaring the seed "good". Also after any non-trivial heading rename
in the source Google Docs — see the orphan-row note below.

**Cost**: each run does 5 small OpenAI `text-embedding-3-small` calls
— roughly $0.0001 total. Negligible, but non-zero, which is why the
suite is opt-in.

**If a test skips with "global_kb tenant not provisioned" or "global_kb
ChromaDB collection is empty"** — the autouse precondition fixture
caught a missing seed step. Re-run `create_tenant --system` /
`seed_kb_from_gdocs` / `embed_pending_kb_documents` and re-run the
smoke suite.

### Orphan rows after a heading rename

`seed_kb_from_gdocs` keys idempotency on the `source_uri` slug, which is
derived from the heading text (transliterated Cyrillic → Latin via
`transliterate.translit(..., "ru", reversed=True)`). **Renaming an H2 or
H3 in the Google Doc breaks idempotency**: the old slug becomes an
orphan (no longer touched by reruns; latest version remains "current"
with stale content) and a new row appears under the new slug.

The retriever ranks by relevance so an orphan with stale wording
naturally sinks below the new row, but operators may still want to
clean up. To find candidate orphans for a given doc:

```python
from collections import defaultdict
from apps.kb.models import KbDocument
from apps.kb.services.global_tenant import get_global_kb_tenant

gkb = get_global_kb_tenant()
# Latest version per source_uri, scoped to one doc:
DOC_ID = "1abc...XYZ"  # the Google Doc ID
prefix = f"gdoc://{DOC_ID}/"
rows = (
    KbDocument.all_tenants
    .filter(tenant=gkb, source_uri__startswith=prefix)
    .order_by("source_uri", "-version")
)
seen: dict[str, KbDocument] = {}
for r in rows:
    seen.setdefault(r.source_uri, r)
# Inspect `seen.values()` — slugs that DON'T match a current heading
# in the live Doc are orphans. Cross-reference manually against the
# Doc's headings (open in Google Docs, scan H2/H3 outline) before
# deleting from the admin.
print("\n".join(sorted(seen.keys())))
```

**Recommended workflow** when renaming a heading:

1. Note the old slug (run the snippet above before the rename).
2. Rename the heading in the Doc.
3. Re-run `seed_kb_from_gdocs` for that doc (creates new row).
4. Delete the orphan row via Django admin (search by the old
   `source_uri`).
5. The K11 pre-delete signal fans out to ChromaDB; no separate Chroma
   cleanup needed.

For mass renames, consider using a one-off script — but think hard:
the previous versions are forensic-relevant. The platform ships with
audit trails on `KbDocument` (Sprint 8 / E1) precisely so old retrieval
answers can be replayed.

---

## Related work

* **Sub-1 / PR #120** — `Tenant.is_system` field + admin delete guard
* **Sub-3 / PR #122** — `KbRetriever` global-fallback for shared doc_types
* **Sub-4 / PR #121** — Google Docs API client (this corpus's source)
* **Sub-4b / PR #129** — Pivot to public Markdown export (no SA credentials)
* **Sub-5 / PR #125** — `seed_kb_from_gdocs` management command (this
  section's source); uses `get_global_kb_tenant()` for the default target.
* **Sub-6 / GH #119** — Seed run on 4 Google Docs + golden-query smoke
  tests (this section).
* **Parent epic** — [#113](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/113)
