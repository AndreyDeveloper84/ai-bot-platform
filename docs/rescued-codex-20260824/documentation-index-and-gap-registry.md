# Documentation Index And Gap Registry

| Field | Value |
| --- | --- |
| Date | 2026-05-30 |
| Scope | `docs/` in `ai-bot-platform-codex` |
| Purpose | Single navigation index for existing docs and prioritized registry of missing docs |
| Status | Initial audit |

## Executive Summary

The `docs/` folder is rich, but it is not yet a controlled documentation system.

Current shape:

- 167 Markdown files.
- 43 PNG assets.
- 2 JSON files.
- 2 HTML experiments.
- 2 SVG files.

Main problem: the project has many good documents, but several indexes are stale, some canonical paths do not exist, and the most important missing docs are exactly around cross-system contracts, salon/admin UX, payments, privacy lifecycle, and release gates.

## Priority Rules

| Priority | Meaning |
| --- | --- |
| P0 | Needed to stabilize production-critical flows or unblock architecture decisions |
| P1 | Needed for MVP completeness, implementation clarity, or operational safety |
| P2 | Cleanup, normalization, archival, or long-term maintainability |

## Existing Document Map

### Root

| Path | Purpose | Status |
| --- | --- | --- |
| `docs/architecture.md` | Original platform architecture overview | Keep as historical/current architecture reference |
| `docs/documentation-index-and-gap-registry.md` | This index and missing-doc registry | New |

### Architecture

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/architecture/project-index.md` | Codebase architecture index by layer/app/API/test areas | Useful, current snapshot |
| `docs/architecture/unified-system-architecture-audit.md` | Main unified Ayla + bot-platform + ai-core architecture audit | Primary audit artifact |
| `docs/architecture/unified-system-stabilization-roadmap.md` | Roadmap from audit findings to implementation phases | Primary stabilization plan |
| `docs/architecture/api-spec-contract-drift-audit.md` | API spec vs implementation drift audit | Important for P0/P1 contracts |
| `docs/architecture/event-contract.md` | Ayla -> bot-platform event contract | Important but currently conflicts with inspected Ayla event names |
| `docs/architecture/event-consumers.md` | Event consumer how-to guide | Needs to stay synced with event contract |
| `docs/architecture/jwt-contract.md` | JWT contract between Ayla and bot-platform | Needs cross-check with current service auth model |

### ADR

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/adr/README.md` | ADR index | Stale: lists only ADR-0001..0008 |
| `docs/adr/ADR-0001-multi-tenant-ready.md` | Multi-tenant architecture | Existing |
| `docs/adr/ADR-0002-three-repo-split.md` | Three-repo split | Existing |
| `docs/adr/ADR-0003-tenant-context-via-contextvar.md` | Tenant context propagation | Existing |
| `docs/adr/ADR-0004-stack-postgres-redis-chromadb-s3.md` | Stack decision | Existing |
| `docs/adr/ADR-0005-multi-llm-provider-routing.md` | Multi-LLM provider routing | Existing |
| `docs/adr/ADR-0006-field-level-encryption.md` | Field-level encryption | Existing |
| `docs/adr/ADR-0007-conversation-state-enum.md` | Conversation state enum | Existing |
| `docs/adr/ADR-0008-role-detection-and-staff-model.md` | Role detection and staff model | Existing |
| `docs/adr/ADR-0009-ayla-split-domain-architecture.md` | Ayla split-domain architecture | Exists but missing from README |
| `docs/adr/ADR-0011-user-personal-context-privacy.md` | UserPersonalContext privacy and retention | Exists but missing from README |

### Specs

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/specs/memory-entry-schema.md` | Tabular companion spec for memory schema | Good model for future schema docs |

### QA

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/qa/ayla-e2e-setup.md` | Ayla E2E setup | Exists, but unified three-repo smoke plan is missing |

### Setup

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/setup/dev-environment.md` | Dev environment setup | Existing |
| `docs/setup/branch-protection.md` | Branch protection setup | Existing |
| `docs/setup/secrets-vault.md` | Secrets vault setup | Existing |
| `docs/setup/protection-main.json` | Branch protection config | Non-md config |
| `docs/setup/protection-dev.json` | Branch protection config | Non-md config |

### Operations

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/operations/global-kb-tenant.md` | Global KB tenant ops | Existing |
| `docs/operations/google-docs-public-link.md` | Google Docs source access | Existing |

### Runbooks

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/runbooks/README.md` | Runbook index | Stale: folder contains more runbooks than listed |
| `docs/runbooks/_template.md` | Runbook template | Existing |
| `docs/runbooks/tenant-onboarding.md` | Tenant onboarding | Existing |
| `docs/runbooks/replay-debugging.md` | Replay debugging | Skeleton/TBD |
| `docs/runbooks/incident-response.md` | Incident response | Existing |
| `docs/runbooks/rollback-procedure.md` | Rollback procedure | Existing |
| `docs/runbooks/security-incident.md` | Security incident | Existing |
| `docs/runbooks/chromadb-auth.md` | ChromaDB auth | Existing |
| `docs/runbooks/strict-scope-flip.md` | Strict tenant scope flip | Existing |
| `docs/runbooks/strict-tenant-refuse-flip.md` | Strict tenant refuse flip | Existing |
| `docs/runbooks/strict-tenant-refuse-flip-quickref.md` | Strict tenant refuse quick reference | Existing |
| `docs/runbooks/strict-tenant-refuse-d2-ceilings-checklist.md` | D-2 ceilings checklist | Existing |
| `docs/runbooks/shadow-mode-launch.md` | Shadow mode launch | Existing |
| `docs/runbooks/canary-ramp.md` | Canary ramp | Existing |
| `docs/runbooks/on-call.md` | On-call | Existing |
| `docs/runbooks/disaster-recovery.md` | Disaster recovery | Existing |
| `docs/runbooks/miniapp-acceptance.md` | Customer Mini App acceptance smoke | Existing |
| `docs/runbooks/m6-auto-draft-suppress-tuning.md` | Auto-draft suppress tuning | Existing |
| `docs/runbooks/master-bot-onboarding.md` | Master bot onboarding | Existing |
| `docs/runbooks/pilot-deployment-part-3-smoke-tests.md` | Pilot master/admin smoke tests | Existing |
| `docs/runbooks/q12a-partial-failure-triage.md` | Reschedule partial failure triage | Existing |
| `docs/runbooks/eventbus-subscriber-activation.md` | Eventbus subscriber activation | Existing |
| `docs/runbooks/server-deployment.md` | Server deployment | Existing |
| `docs/runbooks/telegram-bot-onboarding.md` | Telegram bot onboarding | Existing |
| `docs/runbooks/orders-yookassa-retirement-deploy.md` | Orders/YooKassa retirement deploy | Existing |
| `docs/runbooks/orders-rollback.md` | Orders rollback | Existing |

### Plans

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/plans/2026-05-20-ayla-consolidated-architecture.md` | Consolidated architecture and MVP plan | Existing |
| `docs/plans/2026-05-20-phase-0-sprint-plan.md` | Phase 0 sprint plan | Historical/planning |
| `docs/plans/2026-05-20-phase-0-agent-prompts-index.md` | Phase 0 agent prompts index | Historical/planning |
| `docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md` | Parallel agent runbook | Historical/planning |
| `docs/plans/2026-05-20-phase-0-prompt-stream-alpha.md` | Agent prompt stream alpha | Historical/planning |
| `docs/plans/2026-05-20-phase-0-prompt-stream-beta.md` | Agent prompt stream beta | Historical/planning |
| `docs/plans/2026-05-20-phase-0-prompt-stream-gamma.md` | Agent prompt stream gamma | Historical/planning |
| `docs/plans/2026-05-21-developer-agent-workflow.md` | Developer agent workflow | Existing |
| `docs/plans/2026-05-21-retro-sweep-variant-a.md` | Retro close-out | Historical |
| `docs/plans/ayla-ai-core-roadmap.md` | ai-core roadmap | Existing |
| `docs/plans/phase-1-kickoff.md` | Phase 1 kickoff | Existing |
| `docs/plans/q-att-impl1-port-legacy-bot-tools.md` | Legacy bot tools port plan | Existing |
| `docs/plans/sprint-6-orchestrator-rfm.md` | Sprint 6 RFM/orchestrator plan | Historical/planning |
| `docs/plans/sprint-8-observability-shadow.md` | Sprint 8 observability/shadow plan | Historical/planning |
| `docs/plans/sprint-8-retro.md` | Sprint 8 retro | Historical |
| `docs/plans/sprint-9-internal-smoke.md` | Sprint 9 internal smoke plan | Existing |
| `docs/plans/sprint-9-skill-port.md` | Sprint 9 skill port | Historical/planning |
| `docs/plans/sprint-10-canary-cutover.md` | Sprint 10 canary cutover | Existing |

### Design

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/design/README.md` | Design documentation index | Good, but not synced with `docs/screens` |
| `docs/design/decisions-log.md` | Canonical product/design decision log | Primary design decision source |
| `docs/design/system/design-tokens.md` | Design tokens | Existing |
| `docs/design/briefings/*` | Founder/legal meeting briefings | Existing |
| `docs/design/handoffs/*` | Engineering-ready product/UX handoffs | Broad coverage, some overlap with screens |
| `docs/design/policies/*` | Persistent UX/product policies | Strong coverage |
| `docs/design/legacy/*` | Legacy/reference design docs | Should remain read-only/reference |
| `docs/design/assets/*` | Brand assets and experiments | Non-spec assets |

### Screens

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/screens/ux-screen-inventory.updated.md` | UX screen inventory | Useful but contains missing/stale paths |
| `docs/screens/customer-booking-flow.md` | Customer booking flow | Existing |
| `docs/screens/customer-cancellation-reschedule-flow.md` | Customer cancellation/reschedule | Existing |
| `docs/screens/customer-cancellation-reschedule-flow.updated.md` | Updated cancellation/reschedule | Duplicate/canonical unclear |
| `docs/screens/customer-food-scanner-flow.md` | Customer food scanner | Existing |
| `docs/screens/customer-main-wellness-dashboard.md` | Customer wellness dashboard | Existing |
| `docs/screens/customer-onboarding-flow.md` | Customer onboarding | Existing |
| `docs/screens/customer-profile-flow.md` | Customer profile/privacy | Existing |
| `docs/screens/customer-records-flow.md` | Customer records/history | Existing |
| `docs/screens/customer-reminders-voice.md` | Reminder voice/copy | Existing |
| `docs/screens/master-solo-surface.md` | Solo master surface | Existing |
| `docs/screens/provider-booking-detail-flow.md` | Provider booking detail | Existing |
| `docs/screens/provider-booking-detail-flow.updated.md` | Updated provider booking detail | Duplicate/canonical unclear |
| `docs/screens/provider-calendar-schedule-flow.md` | Provider calendar/schedule | Existing |
| `docs/screens/provider-calendar-schedule-flow.updated.md` | Updated provider calendar/schedule | Duplicate/canonical unclear |
| `docs/screens/provider-calendar-schedule-flow.smart-landing-updated.md` | Smart Landing schedule update | Duplicate/addendum |
| `docs/screens/provider-services-prices-flow.md` | Provider services/prices | Existing |
| `docs/screens/provider-services-prices-flow.smart-landing-addendum.md` | Smart Landing addendum | Addendum |
| `docs/screens/provider-messages-flow.md` | Provider messages through Ayla | Existing |
| `docs/screens/provider-landing-enrichment-flow.updated.md` | Provider landing enrichment | Duplicate/canonical unclear |
| `docs/screens/provider-landing-enrichment-flow.final.md` | Provider landing enrichment final | Probably canonical content, path not canonical |
| `docs/screens/solo-provider-bootstrap.updated.md` | Solo provider bootstrap runbook-like doc | Path mismatch with canonical runbook path |
| `docs/screens/smart-landing-docs-sync-summary.md` | Smart Landing sync summary | Existing |

### Source Materials

| Path | Purpose | Notes |
| --- | --- | --- |
| `docs/source-materials/README.md` | Source material holding area | Existing |

## Structural Documentation Problems

| ID | Problem | Evidence | Priority |
| --- | --- | --- | --- |
| DOC-STRUCT-1 | ADR index is stale | `docs/adr/README.md` does not list ADR-0009 and ADR-0011 | P1 |
| DOC-STRUCT-2 | Runbook index is stale | `docs/runbooks/README.md` lists fewer runbooks than folder contains | P1 |
| DOC-STRUCT-3 | Screen inventory points to paths that do not exist | `docs/screens/provider-onboarding/provider-landing-enrichment-flow.md` is referenced, but folder/file are absent | P1 |
| DOC-STRUCT-4 | Canonical screen files are unclear | Multiple `.updated.md`, `.final.md`, `.smart-landing-updated.md` variants exist | P1 |
| DOC-STRUCT-5 | Design policies and screen docs are separate indexes | `docs/design/README.md` and `docs/screens/ux-screen-inventory.updated.md` do not reconcile coverage | P1 |
| DOC-STRUCT-6 | Planning docs are mixed with active docs | historical sprint plans sit beside active roadmaps without archive marker | P2 |
| DOC-STRUCT-7 | API spec source of truth is outside repo | external PDF/spec folder was audited manually, but generated/current API spec is not stored here | P0/P1 |

## Missing Document Registry

### P0 Missing Docs

| ID | Missing document | Recommended path | Why it matters | Depends on |
| --- | --- | --- | --- | --- |
| M-P0-1 | Cross-system contract registry | `docs/contracts/cross-system-contract-registry.md` | One table for REST, events, auth, env vars, owner service, tests. This is the stabilizing backbone for Ayla + bot-platform + ai-core. | Architecture audit |
| M-P0-2 | Shared event fixture catalog | `docs/contracts/event-fixtures.md` | Current tests can pass with synthetic payloads that do not match the real producer. Need canonical fixture list and version rules. | Event contract, Contract Tests audit |
| M-P0-3 | Internal API contract for bot-platform -> Ayla | `docs/contracts/ayla-internal-api-contract.md` | Payment, recommendations, nutrition, profile, privacy calls need exact paths, auth, request, response, errors. | API spec drift audit |
| M-P0-4 | Service-to-service auth ADR | `docs/adr/ADR-0012-service-to-service-auth.md` | Auth names and headers drift: bearer token, `X-Service-Token`, HMAC, JWT. Needs one accepted rule. | Identity/S2S auth audit |
| M-P0-5 | Booking ownership ADR | `docs/adr/ADR-0013-booking-ownership.md` | Ayla vs bot-platform booking source of truth is the highest-risk domain boundary. Audit exists, but target decision is missing. | Booking ownership audit |
| M-P0-6 | Payment ownership and lifecycle ADR | `docs/adr/ADR-0014-payment-ownership-lifecycle.md` | Payment create/retry/event names/response shape are inconsistent. Needs canonical lifecycle and exposed MVP scope. | Payment flow audit |
| M-P0-7 | Unified E2E smoke and release gate | `docs/qa/unified-system-smoke-gate.md` | Need mandatory smoke for booking, payment retry, tenant denial, privacy, ai-core version across three repos. | Contract Tests audit |
| M-P0-8 | Cross-service privacy/delete/export ADR | `docs/adr/ADR-0015-cross-service-privacy-lifecycle.md` | Current delete/export semantics are split between Ayla and bot-platform. This is user-data critical. | User data lifecycle audit |
| M-P0-9 | Ayla event replay/runbook to bot-platform | `docs/runbooks/ayla-event-replay-to-bot-platform.md` | Once Ayla publishes to bot-platform, operators need replay/DLQ procedure. | Event delivery phase |
| M-P0-10 | Canonical API spec sync plan | `docs/contracts/api-spec-source-of-truth.md` | External PDFs were audited, but the repo lacks a rule for what wins: spec, code, generated OpenAPI, or deviations. | API spec audit |

### P1 Missing Docs

| ID | Missing document | Recommended path | Why it matters | Depends on |
| --- | --- | --- | --- | --- |
| M-P1-1 | Salon main dashboard UX | `docs/screens/salon-main-dashboard-flow.md` | Already marked as next in screen inventory; needed after salon activation. | Provider onboarding docs |
| M-P1-2 | Salon team management UX | `docs/screens/salon-team-management-flow.md` | Needed for masters, roles, service mapping, schedule ownership, deactivation. | Admin/master APIs |
| M-P1-3 | Admin operational queue UX | `docs/screens/admin-operational-queue-flow.md` | Conflicts, transfer tasks, moderation, partial failures, and support escalation need one ops surface. | Observability/support decisions |
| M-P1-4 | Provider main dashboard canonical UX | `docs/screens/provider-main-dashboard-flow.md` | `master-solo-surface.md` exists, but canonical provider dashboard for solo/team/salon is still unclear. | Screens inventory |
| M-P1-5 | Ayla-mediated messaging screen flow | `docs/screens/ayla-mediated-messaging-flow.md` | Policy exists, provider messages exist, but full customer/provider/admin mediated chat flow is missing. | Messaging policy |
| M-P1-6 | Internal chat/support UX | `docs/screens/internal-chat-support-flow.md` | Backend has master/admin internal chat; screen-level support UX is not in `docs/screens`. | internal_chat API |
| M-P1-7 | Customer payment and retry UX | `docs/screens/customer-payment-flow.md` | Payment failure, retry, waiting, capture, refund and confirmation URL need user-facing behavior. | Payment ADR |
| M-P1-8 | Provider payment visibility UX | `docs/screens/provider-payment-visibility-flow.md` | Provider booking detail mentions payment visibility, but payment-specific states are not fully specified. | Payment ADR |
| M-P1-9 | Customer post-visit feedback/review UX | `docs/screens/customer-post-visit-feedback-flow.md` | Feedback endpoint exists; separate UX for review request, complaint, no-show, and provider receipt is missing. | Feedback/reviews handoffs |
| M-P1-10 | Provider notification preferences UX | `docs/screens/provider-notification-preferences-flow.md` | `master_api` exposes notification prefs; need screen states, quiet hours, required vs optional notifications. | Notification ownership audit |
| M-P1-11 | Customer water tracker UX | `docs/screens/customer-water-tracker-flow.md` | Wellness dashboard and water handoff exist, but screen-level tracker flow is absent. | Wellness handoff |
| M-P1-12 | Customer nutrition profile/anketa UX | `docs/screens/customer-nutrition-profile-flow.md` | Onboarding references bot DM anketa; need stable profile editing/retake/consent flow. | Nutrition handoff |
| M-P1-13 | Loyalty wallet/referral UX | `docs/screens/customer-loyalty-wallet-flow.md` | Loyalty policy/handoff exists; screen-level balance/history/apply/referral flow is missing. | Loyalty handoff |
| M-P1-14 | YClients integration ownership ADR | `docs/adr/ADR-0016-yclients-integration-ownership.md` | YClients ownership is architecturally risky and currently split. | YClients audit |
| M-P1-15 | Observability dashboard spec | `docs/specs/unified-observability-dashboard.md` | Audit/logs/analytics are fragmented; need product + ops dashboard specification. | Observability audit |
| M-P1-16 | Contract test strategy | `docs/qa/contract-test-strategy.md` | Need provider-driven/consumer-driven strategy and CI rules. | Contract Tests audit |
| M-P1-17 | AI memory ownership ADR | `docs/adr/ADR-0017-ai-memory-ownership.md` | ADR-0011 covers UserPersonalContext, but unified Ayla/bot memory ownership still needs a target decision. | Memory boundary audit |

### P2 Missing Or Cleanup Docs

| ID | Missing / cleanup document | Recommended path | Why it matters |
| --- | --- | --- | --- |
| M-P2-1 | Documentation ownership policy | `docs/documentation-policy.md` | Defines canonical paths, file suffix rules, update rules, and reviewer expectations. |
| M-P2-2 | Screens canonicalization plan | `docs/screens/canonicalization-plan.md` | Needed to retire `.updated`, `.final`, and addendum confusion. |
| M-P2-3 | Plans archive index | `docs/plans/README.md` | Separates active roadmap from historical sprint plans. |
| M-P2-4 | Design-to-screen coverage matrix | `docs/design/design-to-screen-coverage.md` | Maps design policies/handoffs to screen-level docs. |
| M-P2-5 | Runbook completion checklist | `docs/runbooks/runbook-completion-checklist.md` | Makes skeleton/draft/complete status operationally consistent. |
| M-P2-6 | API docs generation guide | `docs/setup/openapi-generation.md` | Needed once source-of-truth decision is made. |
| M-P2-7 | Legacy docs archive policy | `docs/legacy-docs-policy.md` | Prevents old sprint/design docs from being mistaken for current target behavior. |

## Recommended Formation Order

### Batch 1. Stabilization Docs

These should be formed first because they stop cross-repo drift.

1. `docs/contracts/cross-system-contract-registry.md`
2. `docs/contracts/ayla-internal-api-contract.md`
3. `docs/contracts/event-fixtures.md`
4. `docs/adr/ADR-0012-service-to-service-auth.md`
5. `docs/qa/unified-system-smoke-gate.md`

### Batch 2. Ownership Decisions

These turn audit findings into accepted target architecture.

1. `docs/adr/ADR-0013-booking-ownership.md`
2. `docs/adr/ADR-0014-payment-ownership-lifecycle.md`
3. `docs/adr/ADR-0015-cross-service-privacy-lifecycle.md`
4. `docs/adr/ADR-0016-yclients-integration-ownership.md`
5. `docs/adr/ADR-0017-ai-memory-ownership.md`

### Batch 3. MVP UX Gaps

These unblock implementation clarity for product surfaces.

1. `docs/screens/salon-main-dashboard-flow.md`
2. `docs/screens/salon-team-management-flow.md`
3. `docs/screens/admin-operational-queue-flow.md`
4. `docs/screens/customer-payment-flow.md`
5. `docs/screens/ayla-mediated-messaging-flow.md`
6. `docs/screens/customer-post-visit-feedback-flow.md`
7. `docs/screens/provider-notification-preferences-flow.md`

### Batch 4. Documentation Hygiene

These reduce confusion and make future audits cheaper.

1. Update `docs/adr/README.md`.
2. Update `docs/runbooks/README.md`.
3. Canonicalize `docs/screens/*` duplicate files.
4. Add `docs/plans/README.md`.
5. Add `docs/documentation-policy.md`.

## Immediate Next Actions

| Order | Action | Output | Priority |
| --- | --- | --- | --- |
| 1 | Create `docs/contracts/cross-system-contract-registry.md` | Single contract matrix | P0 |
| 2 | Create `docs/contracts/ayla-internal-api-contract.md` | REST/auth/env contract | P0 |
| 3 | Create `docs/qa/unified-system-smoke-gate.md` | Release smoke checklist | P0 |
| 4 | Draft `ADR-0012-service-to-service-auth.md` | Accepted auth decision candidate | P0 |
| 5 | Draft `ADR-0013-booking-ownership.md` | Accepted booking ownership candidate | P0 |
| 6 | Create `docs/screens/salon-main-dashboard-flow.md` | Next UX screen handoff | P1 |
| 7 | Update stale README indexes | ADR + runbooks synced | P1 |

## Notes

- This file does not replace `docs/architecture/project-index.md`. That file indexes code architecture; this file indexes documentation coverage.
- This file does not replace `docs/screens/ux-screen-inventory.updated.md`. That file indexes UX surfaces; this file tracks all docs and missing documentation across architecture, QA, runbooks, design, and screens.
- External API PDF/spec files under `D:\Мои документы\BeautyGo\Api` were not copied into this repo by this audit. Their drift is tracked in `docs/architecture/api-spec-contract-drift-audit.md`.
