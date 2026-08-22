# Unified Maintainability & Architecture Roadmap

## Status

Living roadmap. Initial version: 2026-05-29. Revised: 2026-05-30 per founder ref.txt review.

Mirrors the structure of `unified-system-stabilization-roadmap.md` (codex integration roadmap).

| Source audit | Output |
|---|---|
| Layer 1 CLI baseline | `audit-baseline-{botplatform,ayla,ayla-ai-core}.txt` |
| Layer 2 hex-graph | `analyze_workspace` results |
| Layer 3 synthesis | this document |
| Companion | `maintainability-audit-findings.md` (raw findings) |
| Companion | `unified-system-architecture-audit.md` (codex integration audit) |
| Companion | `unified-system-stabilization-roadmap.md` (codex integration roadmap) |

## Founder Principle (LOCKED)

**"Integration stabilization has priority over maintainability cleanup. Refactoring is approved only when it either protects the floor, removes proven dead code without touching active P0 areas, or directly supports P0 contract fixes."**

First real PRs must be: **contract matrix, URL/auth, payment contract, event delivery, shared fixtures.** Not code beauty.

## Goal

Lower the per-PR cost of change. Stabilize Ayla ↔ bot-platform integration contracts. Codify ADR-0009 boundaries so future drift is caught in CI.

## Non-Goals

- Do not rewrite working code for style preferences only.
- Do not pause shipping while every clone is extracted.
- Do not block pilot on cosmetic findings.
- Do not refactor `ayla-ai-core` — it is reference quality.
- **Do not delete legacy directories until migration coverage audit confirms 100% ported.**

## Honest Repo Verdicts

| Repo | Verdict | Why |
|---|---|---|
| `ai-bot-platform` | 🔴 **SICK** | 90% of total debt. 3 legacy dirs (NOT delete-ready yet) + 8 E/D hotspots + scattered helpers + 1 fat module. ~29 651 deptry issues (most from legacy). |
| `Ayla djangoproject` | 🟡 **OK** | 12 focused hotspots (1 CC=47), 2 real prod clones, 0 legacy dirs. Targeted refactor. |
| `ayla-ai-core` | 🟢 **REFERENCE** | 6 887 LOC, 22% comments, 0 dead code, max CC=16. Do nothing. Quality bar. |

## Block A — Immediately, BEFORE any big refactor

**Small, cheap, foundational. Parallel-friendly. Do these FIRST.**

| ID | Task | Owner | Effort |
|---|---|---|---|
| A1 | Contract matrix doc (REST + events + auth + envs + owner + tests in single table) | tech-lead + W3 | ~3-4h |
| A2 | Mark payment/booking/eventbus/Ayla clients as contract-frozen in CONTRIBUTING.md | tech-lead | ~30m |
| A3 | PR checklist: endpoint path / auth header / response shape / owner service | W4 | ~1h |
| A4 | Shared `AylaUrlBuilder` (no more `/api/v1/api/v1` drift) | Alpha | ~2-3h |
| A5 | `AYLA_INTERNAL_API_TOKEN` setting + readiness fail-fast | Alpha | ~1h |
| A6 | Fix recommendations path → `/api/v1/internal/me/catalog/recommendations/` | W4 | ~30m |
| A7 | Fix recommendations auth (token + header) | W4 | ~30m |
| A8 | Payment `confirmation_url` parsing (NOT `checkout_url`) + trailing slash + `X-Idempotency-Key` decision | Alpha + W4 joint | ~2-3h |
| **A9** | **ayla-ai-core SHA/version alignment (both consumers same SHA) + startup version smoke** | **Alpha + W2 joint** | **~3-4h** |
| A10 | Minimal shared contract fixtures: `booking.created.v1.json` + `payment.captured.v1.json` + `payment.failed.v1.json` + `recommendations.request.json` | Gamma | ~3-4h |
| A11 | Enforce G1–G10 ADR-0009 import-edges via the existing AST-linter (`tools/lint/`, already CI-blocking) — **Option B**, NOT import-linter (rejected pre-pilot, see #968) | W4 | ~3-4h |

**Block A total:** ~20-25h across 4 streams parallel. ~3-5 working days with parallelism.

### Acceptance Criteria

- [ ] Contract matrix landed.
- [ ] `AylaUrlBuilder` used by all bot-platform Ayla clients.
- [ ] Both consumer repos pin the same `ayla-ai-core` SHA/tag.
- [ ] 4 contract fixtures live in `contracts/` directory.
- [ ] AST-linter (`tools/lint/`) blocks new G1–G10 ADR-0009 import-edge violations in CI; contracts not cheaply expressible in AST are gated by a mandatory Code Reviewer checklist item (Option C fallback).

## Block B — Payment Stabilization

| ID | Task | Owner |
|---|---|---|
| B1 | **DECIDE:** bot-platform may create payments OR retry/display only? | **Founder** |
| B2 | **DECIDE:** Certificate payment in MVP or disable? | **Founder** |
| B3 | Ayla emit ADR vocab: `payment.authorized/captured/failed/refunded` | Alpha |
| B4 | bot-platform consumers accept real Ayla payment fixtures (using A10 contracts) | Gamma |
| B5 | Failed payment E2E smoke (Ayla failed → bot recovery DM smoke) | Alpha + Gamma joint |
| B6 | `AylaPaymentsClient.retry_payment()` client method | W2 |

### Acceptance Criteria

- [ ] B1/B2 founder decisions documented.
- [ ] Live-mode bot payment call either succeeds against Ayla or feature-flagged off.
- [ ] Failed payment triggers `handle_payment_failed()` + `payment_failed` skill in smoke test.
- [ ] Retry callback returns fresh `confirmation_url`.

## Block C — Event Delivery

| ID | Task | Owner |
|---|---|---|
| C1 | **DECIDE:** Existing `OutboxEvent` table is for local / cross-service / both (with split)? | **Founder + Alpha** |
| C2 | Ayla HTTP publisher → bot-platform `/api/v1/internal/events/ingest` | Alpha |
| C3 | Sign payloads with `X-Ayla-Event-Signature` over exact JSON bytes + timestamp | Alpha |
| C4 | Retry/backoff/DLQ fields or table | Alpha |
| C5 | Replay command for stuck/dead events | Alpha |
| C6 | E2E smoke: Ayla booking created → bot-platform `IngestDedupe` | Gamma + Alpha joint |

### Acceptance Criteria

- [ ] Creating/changing Ayla appointment results in bot-platform `IngestDedupe` row.
- [ ] Local notification success cannot mark external delivery complete.
- [ ] Failed HTTP delivery stays retryable + visible to ops.
- [ ] Dead-letter events can be replayed.

## Block D — Booking Ownership Migration

This is the heaviest block. Critical path = `execute_reschedule` rewrite (joint W2+Alpha).

| ID | Task | Owner |
|---|---|---|
| D1 | Freeze new `BookingRequest` writes (AST-linter G5 import-edge + CI gate) | W4 |
| D2 | `AylaBookingClient` (create/cancel/reschedule/list/detail) | Alpha + W2 joint |
| D3 | Booking skill confirm/cancel/reschedule → call Ayla via D2 client | W2 (joint with Alpha) |
| D4 | Disable direct YClients writes for Ayla-owned tenants | W2 |
| D5 | `RemoteBookingProxy` becomes the only bot-platform current-booking mirror | W2 |
| D6 | Reconciliation job (Ayla active count vs bot mirror count per tenant) | W4 + Alpha |

### Acceptance Criteria

- [ ] No customer-facing bot flow creates/cancels/reschedules booking without Ayla.
- [ ] A bot-created booking appears in Ayla mobile history immediately.
- [ ] An Ayla-created booking appears in bot-platform via event delivery.
- [ ] Direct YClients booking writes have one owner.

## Block E — Maintainability Cleanup (parallel where possible)

**Rule:** do not start a maintainability task that touches the same file as an in-flight Block A-D task, **unless joint PR**.

| ID | Task | Owner | When |
|---|---|---|---|
| **E0** | **Legacy migration coverage audit (6 phases per ref.txt founder verdict)** | Agent-driven | Parallel with Block A |
| E1 | Legacy delete (3 dirs) — **ONLY after E0 confirms 100% coverage** | W4 | After Block C earliest |
| E2 | Scattered helpers extract (`_parse_json_body` ×6, `_split_name` ×3, etc.) | W2 (post Tier-A #3) | Parallel with Block D |
| E3 | Fat module split `apps/skills/booking/tools.py` | W2 | After D3 (reschedule rewrite already extracted it) |
| E4 | Test infra DRY (shared conftest fixtures, parametrize 403 tests) | any | Parallel anytime |
| E5 | CI gates: radon pre-commit + vulture pre-commit + jscpd | W4 | After E1+E2 (else baseline noise) |
| E6 | Ayla focused hotspots (`_serialize` CC=47, `_emit`, `compute_norms`, etc.) | Alpha | Post-pilot |

### E0 — Legacy Migration Coverage Audit (founder verdict 2026-05-30)

**Per-phase plan, agent-driven:**

| Phase | Cluster | Files | Effort |
|---|---|---|---|
| E0.1 | AI/LLM layer (`ai_*.py` + `llm.py`) | 11 files, ~5000 LOC | ~2-3h agent |
| E0.2 | MAX handlers (`handlers/*.py`) | 18 files, ~3000 LOC | ~2-3h agent |
| E0.3 | Nudges subsystem (`nudges/*.py`) | 8 files, ~1100 LOC | ~1h agent |
| E0.4 | Misc (`yclients_webhook` / `reminders_factory` / `texts` / `states` / etc.) | ~30 files | ~1-2h agent |
| E0.5 | `legacy_notifications` (`max_bot.py`) | 2 files, 243 LOC | ~30m |
| E0.6 | `legacy_formulatela_mcp` (MCP server) | 10 files, 647 LOC | ~1h |

Per-item decision matrix:
- ✅ **Ported clean** — eligible for delete after Sprint 10 cutover
- ⚠️ **Partial** — gap-fill migration ticket required
- 🔴 **Not ported** — full migration OR explicit defer decision
- 🟡 **Intentionally removed** — document why (e.g. cross_domain cards per `project_cross_domain_insight_safety_gap`)

**Output:** `docs/architecture/legacy-migration-audit-{cluster}.md` per phase + consolidated coverage table.

**Delete authorization:** only when all 6 phases = green AND production-running mysite cutover verified.

## Coupling with Integration Roadmap (codex)

| Maintainability Block | Integration roadmap phase | Joint work |
|---|---|---|
| Block A (A1, A2, A3) | Phase 0 contract freeze | Same PR may carry both |
| Block A (A4, A5) | Phase 1 URL/auth foundation | Same code area |
| Block A (A6, A7) | Phase 1.3, 1.4 recommendations | Direct overlap |
| Block A (A8) | Phase 2.2, 2.3 small payment fixes | Joint |
| Block A (A9) | Phase 6.1, 6.2 ayla-ai-core alignment | **Pulled forward per founder** |
| Block A (A10) | Phase 7.5 contract test CI | **Pulled forward per founder** — minimal fixtures NOW |
| Block A (A11) | Maintainability Phase 0 floor | AST-linter (Option B) protects ADR-0009 import-edges |
| Block B | Phase 2 payment contract | Same scope |
| Block C | Phase 3 event delivery | Same scope |
| Block D | Phase 4 booking ownership | Same scope (joint PRs) |
| Block E | Phase 5 API docs cleanup (parallel) | After decisions stabilize |

## Recommended Execution Order

Per founder ref.txt:

1. Contract matrix + freeze + PR checklist (A1-A3)
2. URL/auth foundation (A4-A7)
3. ayla-ai-core version alignment (A9)
4. Minimal shared contract fixtures (A10)
5. Small payment client fixes (A8)
6. Payment ownership/event decisions (B1, B2)
7. Payment event emit/consume compatibility (B3, B4, B5, B6)
8. Ayla → bot-platform event delivery (C1-C6)
9. Booking ownership migration (D1-D6)
10. YClients kill-switch / ownership decision
11. Catalog/schedule/slot source-of-truth
12. Notification/reminder ownership
13. Provider/master/tenant boundary
14. Privacy/data lifecycle
15. Observability/replay/dashboard
16. Maintainability cleanup where it does not conflict (Block E)

## Release Gates — measure cross-service, NOT just metrics

Per founder ref.txt: success measured by **6 cross-service smoke checks**, not only by CC/MI/clone counts.

### Gate A — Integration Foundation

- [ ] Ayla URL builder used by all bot-platform Ayla clients.
- [ ] Recommendations client passes path/auth contract tests.
- [ ] Enabled Ayla auth secrets fail fast or readiness fails clearly.
- [ ] Both repos pin same `ayla-ai-core` SHA.

### Gate B — Payment Stability

- [ ] Payment create disabled for bot certificate flow OR has canonical Ayla endpoint.
- [ ] Ayla emits payment events bot-platform accepts (real fixture).
- [ ] Failed payment triggers bot recovery smoke test.

### Gate C — Event Stability

- [ ] Ayla booking/payment event observed in bot-platform ingest.
- [ ] Failed delivery is retryable and visible.
- [ ] Replay runbook tested once in staging.

### Gate D — Booking Ownership

- [ ] Bot booking mutations call Ayla.
- [ ] Direct YClients writes from bot booking flow disabled for Ayla-owned tenants.
- [ ] Reconciliation job reports no active drift for pilot tenant.

### Gate E — Maintainability Floor Set

- [ ] AST-linter (`tools/lint/`) blocks new G1–G10 ADR-0009 import-edge violations in CI (Option B).
- [ ] Known existing violations (#925/#927/#928/#968) ticket-tracked and not growing; remediation per Phase 2.2 (NOT a baseline file — no `.importlinter*` exists; see #968 provenance).
- [ ] **Legacy migration coverage = 100% (E0) before any legacy delete.**

## 6 Honest End-to-End Pilot Readiness Checks

Per founder ref.txt — these are the real measure of success, beyond Gates:

1. ✅ Bot booking mutation goes through Ayla
2. ✅ Ayla event reaches bot-platform
3. ✅ Payment failed in Ayla triggers bot recovery
4. ✅ Service-to-service auth works with real headers (live mode)
5. ✅ Same `ayla-ai-core` version loaded in both services
6. ✅ Contract fixtures shared, not synthetic per repo

## First Sprint Candidate (highest leverage, foundational)

Per founder ref.txt explicit list:

| # | Task | ID |
|---|---|---|
| 1 | Contract matrix | A1 |
| 2 | Shared Ayla URL builder | A4 |
| 3 | `AYLA_INTERNAL_API_TOKEN` setting | A5 |
| 4 | Recommendations path fix | A6 |
| 5 | Recommendations auth fix | A7 |
| 6 | `confirmation_url` parsing | A8 |
| 7 | Payment slash/header decision | A8 |
| 8 | ayla-ai-core version/SHA alignment | A9 |
| 9 | First shared fixtures: `booking.created` + `payment.failed/captured` | A10 |

No legacy delete in first sprint. No big refactor. Foundation first.

## Open Decisions

1. Should bot-platform be allowed to create payments, or only retry/display? (B1)
2. Should certificate payment be in MVP, and if yes which Ayla domain owns it? (B2)
3. What is the existing Ayla `OutboxEvent` table for — local, cross-service, or both? (C1)
4. ~~Should `lint-imports.baseline` be a hard count gate from week 1, or warning-only?~~ **RESOLVED (orchestrator 2026-06-03):** enforcement is the `tools/lint/` AST-linter (Option B), not import-linter. New G1–G10 violations hard-fail CI; existing violations are ticket-tracked (#925/#927/#928/#968), not held in a baseline file.
5. ayla-ai-core re-audit cadence — quarterly or only on major version bumps?

## Largest Risk

**Confusing refactoring with architecture repair.** A pretty-split `booking/tools.py` still violates the architecture if it still calls YClients directly and treats `BookingRequest` as source of truth. The 6 end-to-end checks above are the honest measure — not just CC/MI counts.

## References

- `docs/architecture/maintainability-audit-findings.md` — raw findings (this audit)
- `docs/architecture/unified-system-architecture-audit.md` — codex integration audit
- `docs/architecture/unified-system-stabilization-roadmap.md` — codex integration roadmap
- `CLAUDE.md` — ADR-0009 hard rules
- `docs/adr/ADR-0009-ayla-split-domain-architecture.md` — full ADR
- `tools/lint/` AST-linter — enforces G1–G10 ADR-0009 import-edges in CI (Option B; already runs as `red_zone_guard.py`, A11 extends it). import-linter rejected pre-pilot per #968.
- ⚠️ **No `.importlinter*` file exists or has ever existed in this repo.** Earlier references to `.importlinter.passing` / `.importlinter.baseline` (incl. in this doc) were a *planned-as-done* error — A11 never shipped. See the S5 provenance trace on #968. The real legacy_* import ban is ruff TID251 `flake8-tidy-imports.banned-api` in `pyproject.toml`.
- Founder review: `handoffs/ref.txt` (2026-05-30)
