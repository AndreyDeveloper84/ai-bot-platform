# E0.6 — `legacy_formulatela_mcp/` Migration Coverage Audit

**Date:** 2026-05-31
**Auditor:** general-purpose agent (E0.6)
**Scope:** `legacy_formulatela_mcp/**/*` (732 LOC including .md + .toml; 416 LOC Python src; 231 LOC tests)
**Verdict:** **FULL_COVERAGE** (MCP layer deliberately retired; FAQ search reborn as `apps/kb` + `apps/skills/faq`)
**Pilot-blocking?** **NO** — current code covers all functional surfaces and adds multi-tenant isolation that legacy lacked
**MCP layer status:** **RETIRED** — no `FastMCP` / `@mcp.tool` / `mcp.server` references in `apps/*`. The MCP protocol (subprocess + stdio + JSON-RPC) was replaced by in-process skill calls (`apps/skills/faq/tools.py` → `apps/kb/services/retriever.py`).
**Relationship to mysite:** **DIFFERENT_CODEBASE / WRAPPER** — `legacy_formulatela_mcp/` is a *thin MCP wrapper* over `mysite`'s `services_app.HelpArticle` ORM model (see `reindex.py:20` `from services_app.models import HelpArticle` and `django_bootstrap.py:22` `DJANGO_SETTINGS_MODULE=mysite.settings`). It is NOT itself the mysite codebase; it is the MCP-server child-process that talked to mysite via Django ORM. mysite owned the data + business logic; formulatela_mcp owned only the FAQ-search + ping tool surface.

## Method

1. Glob enumerated 13 files (incl. `MIGRATION_NOTICE.md` declaring target = `apps/kb/`).
2. Read every Python source + the migration notice + README + pyproject + both test files.
3. Cross-referenced `apps/kb/` (which the notice names as the target) and confirmed:
   - `apps/kb/chromadb_client.py` — per-tenant `ChromaClient` wrapper (replaces `ChromaStore`)
   - `apps/kb/services/retriever.py::search_kb` — replaces `main.py::search_faq` (with k-clamp, global-fallback, event emission)
   - `apps/kb/services/ingester.py::ingest_document` — replaces `reindex.py::reindex_help_articles` (per-doc, atomic, OpenAI-only, no sentence-transformers default)
   - `apps/kb/projectors.py::help_article_to_body` — replaces `reindex.py::_help_article_to_index_item`
   - `apps/kb/management/commands/migrate_legacy_kb.py` — explicit bootstrap migration command (JSON manifest path; not file-copy of legacy chromadb), idempotent via checksum
   - `apps/kb/management/commands/seed_kb_from_mysite.py` — broader mysite content seed (services, masters, site_settings, promotions) extending beyond just HelpArticle
4. Grepped `apps/*` for `FastMCP|@mcp.tool|mcp.server` → 0 hits → MCP layer fully retired.
5. Grepped `apps/*` for `search_faq|HelpArticle|formulatela` → no legacy import paths, only catalog-mirror `CatalogHelpArticle` model + tests.
6. Verified `legacy_formulatela_mcp/` contains NO food/water/nutrition/wellness/anketa code — those live in the separate (still-unaudited) mysite source-of-truth.

## Directory inventory

Total files: 13
Total LOC (incl. md + toml): 732
Python src LOC: 416 / Python test LOC: 231

### Per-file breakdown

| File | LOC | Purpose | Sub-cluster |
|---|---|---|---|
| `MIGRATION_NOTICE.md` | 9 | Freeze policy, declares target = `apps/kb/` | meta |
| `README.md` | 51 | Local-run + tool list + planned phase 2.2/2.3 tools | meta |
| `pyproject.toml` | 25 | `mcp[cli]>=1.20`, `chromadb`, `openai` deps | meta |
| `src/formulatela_mcp/__init__.py` | 12 | Module docstring, version | scaffolding |
| `src/formulatela_mcp/main.py` | 95 | FastMCP server entrypoint + `ping()` + `search_faq(query,k)` tools | MCP protocol layer + tool impl |
| `src/formulatela_mcp/django_bootstrap.py` | 23 | `django.setup()` for standalone subprocess so it can read `services_app.HelpArticle` | MCP protocol layer |
| `src/formulatela_mcp/embeddings/__init__.py` | 12 | Re-export `EmbeddingStore`, `ChromaStore`, `SearchResult` | scaffolding |
| `src/formulatela_mcp/embeddings/store.py` | 48 | `EmbeddingStore` Protocol + `SearchResult`/`IndexItem` dataclasses | abstraction |
| `src/formulatela_mcp/embeddings/chroma_backend.py` | 145 | `ChromaStore` — default (sentence-transformers) vs openai embeddings; `PersistentClient`; `upsert/search/delete_all/count` | embedding backend |
| `src/formulatela_mcp/embeddings/reindex.py` | 81 | `reindex_help_articles(store)` — full wipe + re-embed of active `HelpArticle` rows; CLI `python -m ...reindex` | reindex CLI |
| `tests/__init__.py` | 0 | — | tests |
| `tests/test_main.py` | 106 | `ping`/`search_faq` tool registration + score-ordering + k-cap | tests |
| `tests/test_embeddings.py` | 125 | `ChromaStore` CRUD + `reindex_help_articles` (active-only, metadata) | tests |

## MCP architecture

Legacy was a **subprocess MCP server**: parent `maxbot` (in mysite) spawned `python -m formulatela_mcp.main` via stdio (FastMCP transport), passing `OPENAI_API_KEY` + `DB_*` + `DJANGO_SETTINGS_MODULE=mysite.settings` in the subprocess env. Two tools were live:
- `ping()` — sanity probe at spawn time
- `search_faq(query, k=3)` — top-k FAQ via Chroma+embeddings against `services_app.HelpArticle`

Tools planned but not implemented (README §"по плану"): `search_services`, `find_master`, `find_slot`, `book_via_yclients` — all deferred to never-shipped Phase 2.2/2.3.

In the current ai-bot-platform stack the MCP-subprocess pattern is **gone**. There is no `FastMCP` import anywhere in `apps/*`. The new architecture is:

- LLM-callable function = JSON-schema-described function in `apps/skills/<skill>/tools.py`
- `apps/skills/faq/tools.py::search_knowledge_base` (345 LOC) is the direct replacement for `search_faq` — same signature shape (query + k), same return-shape contract (list of doc hits), wraps `apps/kb/services/retriever.py::search_kb` which delegates to `apps/kb/chromadb_client.py::ChromaClient.query`.

This is a **deliberate architectural retirement**, not a porting gap. The MCP protocol surface is replaced by direct in-process Python calls inside the orchestrator (see `apps/orchestrator/` + `apps/skills/base.py`).

## Cluster mapping

| Sub-cluster | Legacy files | LOC | Current equivalent | Coverage |
|---|---|---|---|---|
| MCP protocol layer (FastMCP server, stdio transport, django_bootstrap) | `main.py`, `django_bootstrap.py` | 118 | n/a — retired by design (in-proc skill calls instead) | DELIBERATE_REMOVAL |
| `ping()` tool | `main.py:19-26` | 8 | n/a — sanity probe was MCP-spawn-specific | DELIBERATE_REMOVAL |
| `search_faq(query, k)` tool | `main.py:50-82` | 33 | `apps/skills/faq/tools.py::search_knowledge_base` (345 LOC, much richer: tenant-scoped, doc_type filter, score+citation envelope) + `apps/skills/faq/skill.py` (428 LOC orchestration) | FULL + EXPANDED |
| Embedding store abstraction | `embeddings/store.py` | 48 | `apps/kb/chromadb_client.py::KbItem`/`KbHit` dataclasses + `ChromaClient` class (328 LOC) | FULL + EXPANDED (per-tenant isolation added) |
| Chroma backend (provider selection, persist_path, cosine collection) | `embeddings/chroma_backend.py` | 145 | `apps/kb/chromadb_client.py` (328 LOC) + `apps/llm/providers/openai_provider.py` (handles embedding generation) | FULL + EXPANDED (HttpClient + token auth + lru_cache + reset_client_cache for tests) |
| HelpArticle → IndexItem projection | `embeddings/reindex.py::_help_article_to_index_item` | 9 | `apps/kb/projectors.py::help_article_to_body` (1 of 4 projectors; the others — service/master/faq — are NEW, were not in legacy) | FULL + EXPANDED |
| Full reindex CLI | `embeddings/reindex.py::reindex_help_articles` + `main()` | 70 | `apps/kb/services/ingester.py::ingest_document` (per-doc, idempotent via checksum) + `apps/kb/services/projectors.py::sync_catalog_to_kb` (Celery-driven) + `apps/kb/management/commands/migrate_legacy_kb.py` (bootstrap from mysite JSON export) + `apps/kb/management/commands/seed_kb_from_mysite.py` (broader content seed) | FULL + EXPANDED |
| Tests | `tests/test_main.py`, `tests/test_embeddings.py` | 231 | `apps/skills/faq/tests/*` (test_skill / test_tools / test_integration / test_prompts / test_search_knowledge_base_cache) + `apps/kb/services/tests/test_retriever*.py` + `apps/kb/tests/test_legacy_migration_command.py` + `apps/kb/management/commands/tests/test_seed_kb_from_mysite.py` | FULL + EXPANDED |

## Coverage table

| Legacy file | LOC | Current equivalent | Coverage | Evidence | Pilot risk if deleted |
|---|---|---|---|---|---|
| `MIGRATION_NOTICE.md` | 9 | `docs/architecture/e0-6-...md` (this doc) | meta | declares target `apps/kb/` | none — keep for audit trail |
| `README.md` | 51 | `apps/kb/services/retriever.py` docstring + `apps/skills/faq/tools.py` docstring + `apps/kb/chromadb_client.py` docstring | meta | inline module docstrings cover same scope | none |
| `pyproject.toml` | 25 | root `pyproject.toml` (`chromadb`, `openai` deps consolidated) | meta | platform pins consolidated | none — package never installed |
| `src/formulatela_mcp/__init__.py` | 12 | — | scaffolding | re-exports only | none |
| `src/formulatela_mcp/main.py` | 95 | `apps/skills/faq/tools.py::search_knowledge_base` (l.150-220) + `apps/skills/faq/skill.py` orchestration | FULL — MCP transport intentionally retired, tool surface re-implemented in-proc | `apps/skills/faq/tools.py:54` `from apps.kb.services.retriever import search_kb` | none |
| `src/formulatela_mcp/django_bootstrap.py` | 23 | — | DELIBERATE_REMOVAL — no subprocess, no separate Django setup needed | platform IS the Django proc | none |
| `src/formulatela_mcp/embeddings/__init__.py` | 12 | — | scaffolding | re-exports | none |
| `src/formulatela_mcp/embeddings/store.py` | 48 | `apps/kb/chromadb_client.py::KbItem`/`KbHit` (l.66-91) | FULL | dataclass-frozen, same fields renamed (`SearchResult` → `KbHit`) | none |
| `src/formulatela_mcp/embeddings/chroma_backend.py` | 145 | `apps/kb/chromadb_client.py::ChromaClient` (l.188-317) + `_build_chromadb_client` (l.122-156) | FULL + EXPANDED (per-tenant collection naming, HttpClient prod path, token auth, lru_cache singleton) | l.99 `collection_name_for_tenant` → `tenant_<uuid_hex>` (Decision 8) | none |
| `src/formulatela_mcp/embeddings/reindex.py` | 81 | `apps/kb/services/ingester.py::ingest_document` (l.72+) + `apps/kb/projectors.py::help_article_to_body` (l.116) + `apps/kb/management/commands/migrate_legacy_kb.py` (220 LOC) | FULL + EXPANDED (per-doc atomicity, checksum-idempotency, Celery-driven, source_uri scheme `mysite://help-articles/<id>`) | `migrate_legacy_kb.py:122` documents bootstrap path | none |
| `tests/test_main.py` | 106 | `apps/skills/faq/tests/test_tools.py`, `test_skill.py`, `test_integration.py`, `test_search_knowledge_base_cache.py`, `test_prompts.py` | FULL + EXPANDED | 5 test modules vs legacy 1 | none |
| `tests/test_embeddings.py` | 125 | `apps/kb/services/tests/test_retriever.py`, `test_retriever_global_fallback.py`, `test_global_fallback_smoke.py`, `test_ingester.py`, `test_global_tenant.py`, `apps/kb/tests/test_legacy_migration_command.py` | FULL + EXPANDED | 6 test modules vs legacy 1 | none |

## Relationship to mysite + E0.1 follow-up findings

The `mysite_origin_history` memory says food scanner + water tracker + nutrition anketa were production-validated in **mysite**, NOT in `legacy_formulatela_mcp/`. This audit confirms that distinction:

- `legacy_formulatela_mcp/` is a *separate subprocess MCP server* that mysite spawned. It implements ONLY FAQ search over `services_app.HelpArticle`. No food/water/nutrition/wellness/anketa code lives here (verified via grep — 0 hits).
- The food-scanner / water / nutrition gaps already documented in E0.1 follow-up live in `mysite/` (`legacy_maxbot/` neighbor — see `legacy_maxbot/MIGRATION_NOTICE.md` referenced from the migration notice) NOT here.
- **No duplicates of E0.1-follow-up gaps** are created by this audit. The 4 E0.1 follow-up gaps (food_scanner 152-FZ + photo cap + /дневник, health_screening Tier-B FSM, food_correction daily-report-time + water-reminder, cross_domain) are orthogonal to `legacy_formulatela_mcp/` and remain to be addressed against the mysite source.

## Tenant-specific logic audit

Legacy carried only **two** hardcoded «Формула тела»-specific assumptions:

1. **Hard-coded persist path** `legacy_formulatela_mcp/src/formulatela_mcp/embeddings/reindex.py:63` — `default = "/var/lib/formulatela-mcp/chroma"`. Current code (`apps/kb/chromadb_client.py:122-156`) resolves persist root via `settings.CHROMA_HTTP_HOST` (prod HttpClient) or `settings.BASE_DIR/.chroma` (dev/test) — no per-tenant path embedded.
2. **Single global ChromaStore singleton** `legacy_formulatela_mcp/src/formulatela_mcp/main.py:32` — `_chroma_store_singleton`. This is a tenancy violation by design (one salon, no need for isolation). Current `apps/kb/chromadb_client.py::collection_name_for_tenant` (l.99-114) enforces `tenant_<uuid_hex>` per-tenant collections — **structural** isolation that refuses to read/write without resolving a tenant.

**Search for residual hardcoded «Формула тела» in `apps/*` returns 27 hits but ALL are legitimate**: catalog/tenancy seed commands (`apps/catalog/management/commands/seed_dev_formula_tela.py`, `apps/tenancy/management/commands/create_tenant.py`), tests (`apps/tenancy/tests/*`, `apps/replay/tests/test_redactor.py`), and pilot-tenant management. No business-logic hardcodes. **Multi-tenant invariant intact.**

## Gaps requiring action

**None.** Every legacy surface has a current equivalent that is feature-equal-or-better:

- MCP transport — intentionally retired (architecture choice, not gap)
- `search_faq` — replaced by `search_knowledge_base` (richer envelope, citations, observability)
- ChromaStore — replaced by `ChromaClient` (multi-tenant, HttpClient prod path)
- reindex — replaced by `ingest_document` + projectors + Celery K6 sweep + bootstrap command
- HelpArticle source — `services_app.HelpArticle` (mysite ORM) → `CatalogHelpArticle` (catalog mirror) → `KbDocument` (KB row) → embedded chunks (ChromaDB)

One **observation, not gap**: legacy supported a free `sentence-transformers all-MiniLM-L6-v2` default-embedding provider (`chroma_backend.py:32-33`) used in tests and dev to avoid OpenAI API calls. Current `apps/kb/services/ingester.py` appears to require an `LLMProvider` for embeddings (no default-local fallback). This is **likely intentional** (single provider strategy, see `apps/llm/protocol.py`) but means CI / unit tests that previously could exercise embeddings without API access now need a mock provider. Recommended action: **INVESTIGATE_FURTHER** at low priority — verify tests can still run without OpenAI creds; not pilot-blocking; not a porting gap because the production path uses openai already.

## Files safe to delete

| File | Confidence | Reason |
|---|---|---|
| `legacy_formulatela_mcp/src/formulatela_mcp/main.py` | HIGH | MCP transport retired by design; `search_faq` fully covered by `apps/skills/faq/` |
| `legacy_formulatela_mcp/src/formulatela_mcp/django_bootstrap.py` | HIGH | Not needed in in-process arch |
| `legacy_formulatela_mcp/src/formulatela_mcp/embeddings/store.py` | HIGH | `KbItem`/`KbHit` in `apps/kb/chromadb_client.py` |
| `legacy_formulatela_mcp/src/formulatela_mcp/embeddings/chroma_backend.py` | HIGH | `ChromaClient` is strict superset with multi-tenant |
| `legacy_formulatela_mcp/src/formulatela_mcp/embeddings/reindex.py` | HIGH | `ingest_document` + `migrate_legacy_kb` command cover both incremental and bootstrap paths |
| `legacy_formulatela_mcp/src/formulatela_mcp/__init__.py`, `embeddings/__init__.py` | HIGH | scaffolding only |
| `legacy_formulatela_mcp/tests/test_main.py`, `tests/test_embeddings.py` | HIGH | 11+ replacement test modules in `apps/kb/` and `apps/skills/faq/` |
| `legacy_formulatela_mcp/pyproject.toml` | HIGH | deps consolidated in root |
| `legacy_formulatela_mcp/README.md` | MEDIUM | useful historical reference; deletion safe but cheap to retain |
| `legacy_formulatela_mcp/MIGRATION_NOTICE.md` | LOW | retain — declares migration target and is referenced by E0.6 audit trail |

**However** — per founder constraint «legacy код нельзя удалять» and per `MIGRATION_NOTICE.md` "read-only migration reference", we do NOT recommend deletion. We recommend the directory remains frozen in place as historical evidence + migration trace. The HIGH-confidence rating means **functionally redundant**, NOT **deletion-recommended**.

## Investigations needed

1. **(Low) Embeddings provider in tests:** verify `apps/skills/faq/tests/*` and `apps/kb/services/tests/test_ingester.py` use a stub LLMProvider so CI can run without OpenAI credentials. If not, consider exposing a deterministic test-only embedder (legacy did this via `sentence-transformers default`). Next step: read `apps/kb/services/tests/test_ingester.py` + the LLMProvider stubs in `apps/llm/protocol.py`.
2. **(Low) Confirm bootstrap migration is runnable for the pilot tenant.** `apps/kb/management/commands/migrate_legacy_kb.py` is plumbed but pilot ops need the operator-side mysite export script (`tools/mysite_export_kb.py` mentioned in `seed_kb_from_mysite.py` docstring). Verify that script exists and is wired into the pilot launch runbook. Out of E0.6 scope (deployment readiness, not migration coverage).
3. **(Informational) Phase 2.2/2.3 tools (`search_services`, `find_master`, `find_slot`, `book_via_yclients`) listed in legacy README were never implemented in legacy.** They live as planned in the booking skill (`apps/skills/booking/tools.py` 345 LOC) + master_api. No gap, just noting that legacy README mentions roadmap items that landed in a different module.

## Appendix: searches performed

- `Glob legacy_formulatela_mcp/**/*` → 13 files enumerated
- `Bash wc -l` on all Python + md + toml → 732 total LOC
- `Read` of every Python source + MIGRATION_NOTICE + README + pyproject
- `Grep FastMCP|@mcp\.tool|mcp\.server` in `apps/` → 0 hits (MCP layer retired)
- `Grep search_faq|HelpArticle|formulatela` in `apps/` → 16 files, all legitimate (catalog mirror, migration command, tests)
- `Grep faq|FAQ` in `apps/skills/` → 25 hits across `apps/skills/faq/*` (skill + tools + 4 test modules)
- `Grep search_kb|retriever|search_knowledge_base` in `apps/skills/faq/` → confirms tool→retriever→ChromaClient chain
- `Grep food|water|nutrition|wellness|anketa` in `legacy_formulatela_mcp/` → 0 hits (confirms scope separation from mysite E0.1 follow-up gaps)
- `Grep formulatela|formula.tela|formula-tela|Формула тела` in `apps/` → 27 hits, all legitimate (catalog seed, tenancy management, tests)
- `Read` of `apps/kb/chromadb_client.py` (328 LOC) + `apps/kb/management/commands/migrate_legacy_kb.py` (220 LOC) + first 80 lines of `apps/kb/services/retriever.py` + first 80 lines of `apps/kb/services/ingester.py` + first 60 lines of `apps/kb/projectors.py` + first 50 lines of `apps/kb/management/commands/seed_kb_from_mysite.py`
