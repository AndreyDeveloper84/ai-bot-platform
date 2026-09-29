# Ayla Memory vs KB — forensic audit

Дата аудита: 2026-08-24. Объём: read-only статический аудит веток `dev`/`main` локальных клонов `ai-bot-platform`, `ayla-knowledge`, `beautygo_backend` (`djangoproject-catalog`) и `ayla-ai-core`. Live readback, destructive actions и booking mutations не выполнялись.

Уровни доказательств:

- `VERIFIED` — цепочка подтверждена production-кодом/моделью и независимым тестом или cross-repo контрактом.
- `INFERRED` — вывод из кода без live database/pilot readback.
- `NEEDS LIVE READBACK` — фактическое заполнение/достижимость в deployed environment не может быть установлено из исходников.

## 0. Executive Verdict

Итоговый классификатор: **C — MULTIPLE COMPETING MEMORY SYSTEMS**.

В runtime одновременно существуют: (1) bot-owned encrypted `MemoryEntry` + `UserPersonalContext` для green conversational facts, (2) backend-owned `users.UserPersonalContext` для declared profile/preferences, (3) `Conversation`/`Message` как долгоживущая история диалога, (4) `ClientProfile`/booking/nutrition как derived или transactional data. Между двумя semantic stores есть best-effort bridge, но нет единого канонического retrieval/purpose envelope.

| Capability | Verdict | Severity |
|---|---|---:|
| Write | PARTIAL | P1 |
| Consent | PARTIAL | P0 |
| Persistence | PARTIAL | P1 |
| Retrieval | PARTIAL | P1 |
| Relevance / purpose selection | MISMATCH | P0 |
| Prompt rendering | PARTIAL | P1 |
| Provenance / trust | PARTIAL | P0 |
| Correction | PARTIAL | P1 |
| Forget one | PARTIAL | P1 |
| Forget all | PARTIAL | P0 |
| Export | PARTIAL | P0 |
| Account delete | PARTIAL | P0 |
| TTL / aging | PARTIAL | P1 |
| Conflict / supersede | PARTIAL | P1 |
| Goal memory | NOT IMPLEMENTED | P1 |
| Diet / food memory | PARTIAL | P0 |
| Health-signal safety | PARTIAL | P0 |
| Cross-tenant isolation | PARTIAL | P0 |
| User visibility | PARTIAL | P1 |
| Recommendation use | PARTIAL | P1 |
| WHY provenance | NOT IMPLEMENTED | P1 |

Главный privacy/safety gap: canonical KB требует proposal/consent/purpose-gate для semantic memory и relevance-filtered ContextEnvelope, но production green write и prompt read используют более узкую legacy-модель `PERSONAL_DATA`/`memory_green`, а `consent_scope`, `purpose_tags`, evidence и confidence не являются обязательными runtime decision inputs.

Главный runtime gap: declared backend context и bot `MemoryEntry` зеркалятся best-effort, но читаются двумя разными путями и передаются модели как плоские display strings; удаление/экспорт не доказаны end-to-end для всех источников.

Счёт доказанных расхождений в матрице: **P0 — 7, P1 — 12, P2 — 3**. P0 означает фактическое правовое/безопасностное или pilot-impact последствие, а не просто несоответствие названиям.

## 1. Canon Sources

Использованные canonical/near-canonical документы:

| Path | Version / status | Relevant sections |
|---|---|---|
| `ayla-knowledge/05 Architecture/Ayla Memory Domain Contract.md` | v1.0, approved, accepted, canonical approved, updated 2026-08-20 | §§3–4 entities/provenance; §5 lifecycle; §6 conflict; §7 session/persistent; §§8–10 ownership/delete; §12 gates |
| `ayla-knowledge/05 Architecture/Ayla Context Resolution Contract.md` | v1.0, approved, accepted, canonical approved, updated 2026-08-20 | §§3–5 ContextEnvelope and 12-step pipeline; §6 consumers; §8 legacy read paths |
| `ayla-knowledge/06 Safety and Governance/Consent Scope Registry.md` | v1.4, approved, canonical approved, updated 2026-08-20 | §§2–5 categories/scopes; §6 runtime authorization; §§8–10 commands, audit, activation; §12.3 export |
| `ayla-knowledge/06 Safety and Governance/Data Inventory Matrix.md` | v1.0, draft, updated 2026-07-24 | §2 matrix; §§3–4 boundaries. Used as governance evidence, not as sole canon because status is draft. |
| `ayla-knowledge/01 Product/BOT-001 First Contact Specification.md` | v1.0, approved, accepted, canonical approved, updated 2026-08-12 | §§4, 11, 14, 20: progressive collection and consent on demand |
| `ayla-knowledge/05 Architecture/AMD-020 C5 Implementation Amendment.md` | v0.8.1, draft, proposed, updated 2026-08-06 | implementation candidate only; not used to override approved documents |
| `ayla-knowledge/05 Architecture/ADR-0012 Dynamic User Model.md` | v0.2, draft, proposed, updated 2026-07-21 | proposal-first design candidate; not an approved runtime contract |

Important KB conflict/gap: approved Memory Domain Contract §3.2/§4 and Consent Scope Registry §6/§10 require `MemoryProposal`, purpose/consent resolution and explicit provenance/confidence semantics, while the implementation amendment and current runtime describe a green silent-remember path. The amendment and ADR-0012 are draft/proposed, so they cannot silently supersede the approved rule. This is recorded as `KB CONFLICT`, separately from code defects.

## 2. Memory Vocabulary

| Term | Actual meaning in code | Classification |
|---|---|---|
| `MemoryEntry` | Encrypted JSON fact keyed by canonical Ayla UUID; green/yellow/red zones; bot-owned | persistent semantic memory |
| `UserPersonalContext` in `ai-bot-platform` | Parent/read model for bot memory, summary, deletion tombstone and minor lock | persistent semantic memory/read gate |
| `UserPersonalContext` in `beautygo_backend` | Declared mobile/in-app profile fields: districts, time, budget, diet, sensitivities, favorites and derived busy days | persistent profile/preferences, not the same model |
| `Conversation`/`Message` | Stored dialog and current skill/action state | transactional/session history; it is still personal data and can be prompt input |
| `ClientProfile` | RFM/LTV/risk/tier snapshot derived from booking facts | derived analytics, not user-stated memory |
| `Observation` | Inferred/signal rows or backend inference outputs | derived/observed; must not be represented as user-stated |
| `Candidate` / `Proposal` | `GreenFactCandidate` exists in extractor; no production `MemoryProposal` persistence/approval flow found | candidate exists; canonical proposal flow not wired |
| `Goal` | No connected bot memory model/write/retrieval path found in audited repos | INFERRED NOT IMPLEMENTED |

## 3. Actual Memory Architecture

~~~mermaid
flowchart LR
  U[MAX / Mini App / mobile user]
  H[ai-bot-platform handler]
  C[consent gate]
  E[extract_user_facts]
  L[local MemoryEntry + bot UserPersonalContext]
  B[best-effort PATCH bridge]
  P[beautygo_backend users.UserPersonalContext]
  R1[local read_current_view / memory commands]
  R2[get_declared_prefs]
  M[ai-bot-platform prompt assembly]
  CORE[ayla-ai-core build_memory_block / render]
  LLM[LLM concierge]
  UX[reply / show / forget]
  U --> H --> C
  H --> E --> L
  E --> B --> P
  L --> R1 --> M
  P --> R2 --> M
  M --> CORE --> LLM --> UX
  UX --> H
~~~

Evidence: `ai-bot-platform/apps/channels/max/handler.py:906-1082,1111-1131,1237-1246`; `apps/orchestrator/memory/personal_context.py:43-157`; `apps/orchestrator/memory/ayla_bridge.py:76-211`; `apps/orchestrator/memory_block.py:78-170`; `ayla-ai-core/src/ayla_ai_core/memory.py:71-170`.

## 4. Storage Inventory

| Storage / mechanism | Repo / model | Contains | Source/class | Persistent / TTL | Consent | Read by runtime | User-visible |
|---|---|---|---|---|---|---|---|
| `MemoryEntry` | `ai-bot-platform/apps/identity/models.py:622-947` | green/yellow/red JSON facts, kind, source, lifecycle/deletion metadata | explicit, inferred, signal | DB; green `ttl_days=NULL` by default; yellow 365/red 90 intended | green gated by `PERSONAL_DATA`; yellow/red field requires consent but writer’s minor guard is separate | local prompt surface, show/forget | green via chat; UI/API not proven |
| bot `UserPersonalContext` | same, `:535-619` | summary, preferred display/language, forget tombstone, minor lock | system/read model | DB; soft-delete/tombstone | read gate | local reader/surface | summary/facts via chat |
| backend `UserPersonalContext` | `djangoproject-catalog/users/models.py:478-590` | districts, time slots, budget, diet, skin sensitivities, favorites, cancellation, service fields | declared + inferred `busy_days`/favorites | DB; no field TTL in model | no memory-specific consent gate in view | backend AI chat and bot bridge | GET/PATCH/DELETE API |
| `Conversation` / `Message` | bot `apps/conversations/models.py:55+` and AI backend `ai/models.py` | raw/redacted message history, action/tool state | user statement / transactional | DB; recent history cap in core, no erasure proof for all replicas | conversation storage, not memory gate | concierge history | chat transcript |
| Redis / booking/session | bot session/booking modules | current journey, pending memory question, booking continuation | session | bounded in some paths; e.g. booking context 900 sec; not a memory store | session processing | current turn | not directly |
| `ClientProfile` | `ai-bot-platform/apps/identity/models.py:359+` | RFM/LTV/risk/tier aggregates | derived from booking | DB/cache; daily/on booking refresh; no memory TTL | no memory consent | not proven in MAX memory block; backend prompt may use booking count | not proven |
| nutrition aggregate | backend nutrition endpoint via `apps/orchestrator/nutrition_context.py:134-221` | 7-day aggregate, protein percentage/streak, sanitized hint | raw wellness derived aggregate | backend retention; prompt feature flag default OFF | PERSONAL_DATA + HEALTH | only if flag and both consents | not a memory list |

The two `UserPersonalContext` classes are explicitly documented as different concepts in `ai-bot-platform/apps/identity/models.py:543-552`; the backend model is not a canonical substitute for `MemoryEntry`.

## 5. Source-of-Truth Matrix

| Fact | Source of truth | Cache/mirror | Write authority | Conflict policy | Evidence |
|---|---|---|---|---|---|
| Channel identity | bot `BotUser` scoped by `(tenant, channel, channel_user_id)` | `ayla_user_id` bridge | bot identity resolver | multiple shells resolve to one UUID or fail closed in privacy service | `apps/identity/models.py:47-87`; `apps/identity/services/privacy.py:177-237` |
| Declared districts/time/budget/diet | backend `users.UserPersonalContext` | bot `MemoryEntry` mirror for extracted green facts | backend API + bot bridge | LWW/union in bridge | backend `personal_context_views.py:122-175`; bridge `:126-158` |
| Explicit conversational fact | bot `MemoryEntry` | backend fields where mappable | extractor + `record_explicit_green_facts` | dedup; single-cardinality values supersede old rows | `personal_context.py:81-157` |
| Favorite master | backend or engine-derived field; bot cannot bridge name to UUID | bot local row may exist | backend engine / bot local | not one coherent policy | `ayla_bridge.py:17-31,59-68` |
| Booking history | booking/appointments backend | `bookings_count` prompt scalar | booking domain | transactional authority; not memory | `ai-bot-platform/apps/orchestrator/concierge.py` and backend `appointments` |
| RFM/lifecycle/churn | bot `ClientProfile` derived cache | none proven | identity services/scheduled recompute | recompute | `apps/identity/models.py:359-407` |
| Food log/nutrition | backend Wellness/Nutrition domain | weekly aggregate in prompt | nutrition service | domain retention | `apps/orchestrator/nutrition_context.py:12-37,193-250` |

There is no explicit conflict policy for city, price, favorite master or goal across bot and backend stores. Diet/time/district have partial code-level mapping only.

## 6. Write Pipeline

### 6.1 Explicit conversational green fact

`MAX Update → handler → consent → extract_user_facts → candidate list → Ayla UUID resolution → local dedup → MemoryEntry write → single-key supersession → best-effort backend PATCH`.

Evidence: `apps/channels/max/handler.py:1237-1246`; `apps/orchestrator/memory/personal_context.py:43-79,81-131,144-157`; `apps/identity/services/memory_writer.py:177-210`.

The writer stamps explicit rows with `status=active`, `provenance=user_stated`, `effective_from`, `updated_at`, and optional `expires_at`; it does not fabricate `consent_scope`, `source_event_id`, `evidence_refs`, `derivation_method`, or `purpose_tags` (`memory_writer.py:177-196`). `request_id` is generated, but is not stored as an origin-message reference in `MemoryEntry`.

### 6.2 Inferred/signal writer

`record_inferred_green_facts` exists at `apps/identity/services/memory_inferred.py:80-132` and is consent-gated/deduplicated, but production call-site search found no MAX handler invocation; references are tests and service code. Therefore it is `WRITE CAPABILITY / REACHABILITY NEEDS LIVE READBACK`, not proven active pilot behavior.

### 6.3 Candidate/proposal

The extractor’s `GreenFactCandidate` is real. A durable `MemoryProposal`/`DecisionRecord` class, user approval state, candidate rejection flow, or proposal audit was not found in production code. The canonical `MemoryEntry` schema contains proposal-compatible fields, but the model comments explicitly state they are not consulted by current read/write paths (`apps/identity/models.py:841-849`). Verdict: **NOT IMPLEMENTED** for the canonical proposal-first pipeline.

## 7. Consent / Policy

| Memory class | KB requirement | Code enforcement | Result |
|---|---|---|---|
| session context | no persistent consent; session authorization | current handler continues normal processing | SUPPORTED |
| persistent semantic memory | `preference_memory`, purpose gate, proposal/consent audit | green writer checks global `PERSONAL_DATA`; `memory_green` controls backend retrieval/prompt, but `MemoryEntry.consent_scope` remains null | MISMATCH, P0 |
| yellow/red | explicit consent, safety/minor guard, restricted reader, audit | model/check constraints and writer minor-protection fail-closed; no complete proposal/purpose runtime | PARTIAL, P0 |
| nutrition/health prompt | PERSONAL_DATA + HEALTH, feature off by default | `_consent_open` checks both and returns empty on failure | SUPPORTED for current disabled/guarded path |
| consent revocation | revoke must invalidate reads and trigger deletion | local gates exist; cross-store/cache/export cascade not proven | PARTIAL, P0 |

KB: `Consent Scope Registry.md:439-495,497-637,761-802,941-970,1065-1113`; `Data Inventory Matrix.md:39-48,81-95`.

Code: `apps/consent/memory.py:47-55`; `apps/channels/max/handler.py:906-918`; `apps/orchestrator/nutrition_context.py:175-190`; `apps/orchestrator/memory_writer.py:177-186`.

The broad green gate is real and fail-closed, but it is not equivalent to the approved `preference_memory` purpose gate. This is the principal consent divergence.

## 8. Persistent Storage and Provenance

`MemoryEntry.content` is Fernet-encrypted, but `UserPersonalContext.summary` is explicitly plaintext because it is sent to LLM context (`apps/identity/models.py:584-590`). Persistent rows have source (`explicit/inferred/signal`), `last_inferred_at`, `created_at`, `last_used_at`, `ttl_days`, consent/deletion fields and canonical provenance. They do **not** have a confidence field; this is explicitly asserted by test `apps/identity/tests/test_memory_entry_step35_write_compat.py::test_no_confidence_field`.

Present: `SOURCE`, `CREATED_AT`, `UPDATED_AT` (only explicit writes), `PROVENANCE`, `CONSENT_SCOPE` (nullable), `EXPIRES_AT` (nullable), `EVIDENCE_REFS` (default empty), `DERIVATION_METHOD` (nullable).

Missing or not reliably populated: origin message/event for explicit write, confidence, `last_confirmed_at`, mandatory consent scope, mandatory purpose tags. `source_event_id` is a schema field but writer does not supply it. Verdict: **PARTIAL / P0** because inferred vs user-stated cannot be reliably reconstructed for all rows and the prompt does not receive provenance.

## 9. Retrieval / Relevance / Prompt

Actual MAX retrieval path:

1. `handler.py:1056-1077` reads local current green view and builds a separate ai-core memory block.
2. `memory_block.py:87-102` calls gated backend declared prefs, merges local inferred/explicit green facts, with declared values winning conflicts.
3. `handler.py:1117-1131` passes `extra_system=personal_context_block`, `memory_block` and `nutrition_block` through the concierge seam.
4. `ayla-ai-core` receives already-rendered strings; it does not access storage or consent.

The ai-core memory builder applies a maximum of eight rendered facts and fixed field order (`ayla-ai-core/src/ayla_ai_core/memory.py:49-60,71-94,109-170`). It applies confidence only if the caller supplies it. The bot adapter sets declared confidence `1.0` and inferred confidence `0.6` (`apps/orchestrator/memory_block.py:91-102,108-170`). However, the source/provenance, timestamp, consent scope, evidence and purpose are discarded before prompt rendering. The LLM sees labels such as “diet” or “preferred time”, not a structured ContextEnvelope.

The prompt does not receive all `MemoryEntry` rows: only green current view plus declared fields. This is safer than `ALL MEMORY`, but it is not the KB-required purpose/sensitivity/relevance resolver. Red/yellow are excluded from the standard memory block; backend skin sensitivities are included by `ai.personal_context_hint.py:107-117` in the separate backend AI chat path, so two consumers have different sensitivity behavior.

No vector retrieval or embedding memory was found. Short-term conversation history is separately capped by ai-core history limit/token budget, not merged into persistent memory (`ayla-ai-core/src/ayla_ai_core/orchestrator.py:7-19,92-100`).

## 10. ayla-ai-core Boundary

| Layer | Owner | Actual responsibility | Policy responsibility |
|---|---|---|---|
| beautygo backend | W2/Wellness/booking domains | declared profile, booking and nutrition facts; own API auth/validation | source-domain policy |
| ai-bot-platform | identity/orchestrator | consent lookup, local memory write/read/delete commands, bridge, selection, prompt blocks | most runtime gates, but legacy scope model |
| ayla-ai-core | pure Python library | render supplied green memory block, cap/order/soften confidence; orchestrate history/LLM/tools | no storage, consent or schema authority |
| LLM | external model | language/tool selection and response text | must not be trusted as memory/ranking/provenance authority |

Evidence: `ayla-ai-core/src/ayla_ai_core/memory.py:1-11,71-94`; `orchestrator.py:21-32`; `apps/orchestrator/memory_block.py:1-25`.

Boundary conclusion: retrieval ends in bot-platform before `build_memory_block`; prompt rendering begins in bot-platform/core. Core cannot enforce consent and cannot distinguish a user-stated fact from an inferred fact after the adapter flattens both to strings.

## 11. Conflict / Supersede / Correction

For bot green facts, same-key duplicate values are deduplicated. A new value for single-cardinality keys supersedes live old rows with `supersession_reason=changed` (`personal_context.py:81-131`, `memory_writer.py:213-230`). The old row remains in database history and is excluded by current read policy. This is VERIFIED for explicit bot facts.

For backend declared fields, time and districts union-merge, price overwrites bounds, diet uses LWW, and forget clears diet/time/district but cannot clear price because the frozen API has no honest null encoding (`ayla_bridge.py:9-31,126-158,176-211`). Favorite-master names cannot be bridged to UUIDs and are logged as a contract gap. There is no equivalent verified conflict policy for city, goal, or cross-store contradictory values.

Ordinary correction can work for recognized explicit facts: the next explicit statement is extracted, supersedes the local single-cardinality row and may PATCH backend. It is not a general “memory correction” protocol and does not update all mirrors atomically. The user-facing path is chat commands, not a structured proposal review.

## 12. Forget / Delete / Export / Account Deletion

### 12.1 Chat controls

`handle_memory_command` supports show, one-field/domain forget and two-step forget-all (`apps/persona/memory_commands.py:230-275`). One-fact deletion soft-deletes local green rows and writes audit evidence. Forget-all first records `forget_all_requested_at`, read gates immediately return empty, then async sweep soft-deletes entries (`apps/identity/services/memory_deleter.py:87-112`; `memory_reader.py:21-67`). This is good local behavior and prevents immediate re-learning (`apps/channels/max/handler.py:1241-1246`).

Known gap: bridge clearing intentionally skips `price_range` and `favorite_masters` (`ayla_bridge.py:187-211`), so “forget all” is not proven to remove all backend declared values. The backend personal-context API can wipe the whole row or one field (`djangoproject-catalog/users/personal_context_views.py:122-241`), but this is a separate authenticated surface, not proven to be invoked by MAX forget-all.

### 12.2 Deletion cascade map

| Entity | Actual behavior | Verdict |
|---|---|---|
| local `MemoryEntry` | soft delete/tombstone; physical purge intended after 30 days | VERIFIED local only |
| local bot UPC | soft-delete/tombstone, hard-delete forbidden by model contract | VERIFIED local |
| backend `UserPersonalContext` | own DELETE API hard-deletes row; next GET recreates empty row | VERIFIED backend API |
| bot `BotUser` | account delete scrubs PII but keeps routing key/identity tombstone | VERIFIED model/service; prompt exclusion needs deployment readback |
| Conversation/Message | retained for operational/history/audit; complete account-delete cascade to prompt source not proven | P0 gap / NEEDS LIVE READBACK |
| Redis/session caches | no universal memory invalidation proof found | P1 gap |
| analytics/audit | event metadata retained by design; value-level cascade not established | NEEDS LIVE READBACK |
| embeddings/vector index | none found | NOT APPLICABLE in audited code |

Export has bot privacy machinery and backend personal-data endpoints, but a single export containing local MemoryEntry, backend UPC, conversations, derived ClientProfile and nutrition is not proven. `apps/identity/services/privacy.py` contains cross-shell resolution and export/delete orchestration, yet upstream reachability and live payload completeness remain NEEDS LIVE READBACK.

## 13. TTL / Aging and Session vs Persistent

Green memory defaults to no automatic TTL (`models.py:802-807`); `expires_at` is populated only when a caller passes `ttl_days` (`memory_writer.py:188-196`). Yellow/red defaults are described in model docs but automatic sweep/read enforcement is not demonstrated for every path. Backend declared preferences have no TTL fields. Redis booking state is temporary (e.g. 900-second booking context), while `Conversation` and backend context are DB-persistent.

Therefore “preferred time” and diet can be long-lived until correction/delete; there is no verified `last_confirmed_at` refresh or stale-state UX. This conflicts with KB’s persistent context lifecycle and purpose-bound retention requirements.

## 14. ClientProfile / Derived Analytics

`ClientProfile` is explicitly a derived RFM/LTV/risk/tier refresh-on-write cache from booking facts (`apps/identity/models.py:359-407`). It is not included in the bot memory block by the audited code, although booking count is used for tone. Backend `favorite_masters` and `busy_days` can be generated by `users/personal_context_inference.py:157-190`; they are returned by the user profile serializer and can enter backend AI prompt hints. These are inferred/derived, not user-stated. Provenance is only a `data_sources` JSON marker in backend context and is not rendered to the LLM. Treating these as “what user told Ayla” would be incorrect.

## 15. Goal Memory

No production `Goal`/transformation-goal write, confirmation, lifecycle, retrieval or correction path was found in the audited runtime. `diet_type` is a nutrition preference and must not be called a transformation goal. Verdict: **NOT IMPLEMENTED / P1** for memory; any claim that a user goal is remembered is INFERRED from product documents, not VERIFIED in code.

## 16. Food / Diet / Wellness / Health

The systems distinguish some layers, but not consistently:

- Food logs and photos remain in the nutrition domain; the bot can fetch only a weekly aggregate when `CONCIERGE_NUTRITION_CONTEXT_ENABLED` is true.
- Diet type is a persistent declared backend field and also a local green MemoryEntry candidate.
- Food-derived inferred health conclusions are not persisted by the normal green extractor; yellow/red writes are minor-protection gated and red reads require a dedicated reader by contract.
- Backend `skin_sensitivities` is profile data and is rendered in backend AI prompt hint; it is not the same as local red MemoryEntry.
- Nutrition aggregate includes a free-form Ayla hint after sanitization and is surrounded by a medical boundary (`nutrition_context.py:19-37,39-50,156-169`).

The strongest verified safety property is fail-closed HEALTH consent and feature-off default. The remaining P0 is cross-domain semantic mismatch: the KB forbids sensitive inference by default, while the backend profile/prompt path can expose sensitivity fields without the same local red-zone reader/provenance envelope. Whether this is reachable for a specific production client needs live endpoint/pilot readback.

## 17. Multi-Tenant / Multi-Bot Identity Isolation

`BotUser` is tenant-scoped by channel identity, but bot `UserPersonalContext` and `MemoryEntry` are deliberately keyed by global canonical `ayla_user_id` and use plain managers. This permits cross-provider reuse by design; the model help text says `source_tenant_id` is informational and not a scope boundary (`apps/identity/models.py:768-775`).

The privacy service fail-closes when channel shells disagree on Ayla IDs (`privacy.py:177-237`), which is a strong local control. However, the prompt bridge does not carry a purpose/tenant envelope and declared backend context is global for the linked user. KB requires approved cross-provider reuse and prohibits provider-confidential facts. The enforcement of that distinction is not proven at every read. Verdict: PARTIAL / P0.

Two BotUsers for one person across tenants are expected; two different people sharing the same canonical ID would be catastrophic, but no live identity graph read was performed. This item is NEEDS LIVE READBACK for deployed data quality.

## 18. Memory → Recommendation / WHY

Verified direct uses:

| Fact | Actual use | Evidence |
|---|---|---|
| preferred time slots | rendered to prompt; also booking time-preference path | `memory_block.py:91-99`; `handler.py:1249-1278` |
| districts | rendered to prompt; backend search/recommendation use is not proven in MAX | `memory_block.py:91-99`; backend hint |
| price | rendered to prompt; bridge overwrites backend bounds | `ayla_bridge.py:141-147` |
| diet | rendered as green prompt text; nutrition aggregate is separate | `memory_block.py:144-157`; `nutrition_context.py:134-169` |
| favorite master | bot/core display path can render names/IDs only if available; bridge cannot resolve names | `ayla_bridge.py:17-25`; `ayla-ai-core/memory.py:121-126` |
| goal | no verified use | no production evidence |

The current concierge prompt is tool-first and can select `show_masters`; there is no structured `recommendation_id`, evidence list, or WHY contract attached to a memory fact. The LLM can phrase a relationship between prompt memory and a result, but runtime does not verify that relationship. Verdict: **WHY NOT IMPLEMENTED / P1** and **recommendation use PARTIAL / P1**. Three memory-driven WHY scenarios therefore require live observation to establish exact user-visible text, but code proves absence of a binding evidence object.

## 19. User Visibility and “Что Ayla знает”

Chat visibility exists: `memory_commands.py` renders current local green facts and supports forget. Backend has GET personal-context and per-field/whole-context DELETE APIs (`djangoproject-catalog/users/personal_context_views.py:122-241`). There is no verified unified Mini App route that lists both bot `MemoryEntry` and backend declared fields with source, timestamp, confidence, consent scope and deletion status. Therefore silent remembering has partial discoverability, not complete discoverability.

## 20. Logging / PII / Cache

Positive evidence: memory write logs use user UUID/count/kind, not fact values (`personal_context.py:133-142`); nutrition block logs length only (`handler.py:1100-1108`); BotUser docs prohibit raw phone in AuditLog. Risk: backend logs user IDs/field names on wipe/reset and prompt observability/audit infrastructure stores model-call metadata. A full log configuration/live sink review was not performed. Verdict: NEEDS LIVE READBACK for retention and redaction.

No universal memory cache invalidation hook was found. Local read paths query DB and gate tombstones; backend declared context and any HTTP/client caches rely on client behavior. After a correction/delete, stale prompt data is possible until the next fetch; this is an inferred P1 risk, not proven from a deployed cache hit.

## 21. Tests

Relevant meaningful tests exist for local consent gate, explicit write/dedup/supersession, read-gate, forget-one/forget-all, identity invariant, memory block, nutrition consent and prompt injection. Examples: `apps/orchestrator/tests/test_personal_context_write.py`, `apps/orchestrator/tests/test_memory_bridge.py`, `apps/identity/tests/test_memory_entry_step35_write_compat.py`, `apps/identity/services/tests/test_memory_deleter.py`, `apps/channels/tests/test_global_memory_commands.py`, `apps/channels/tests/test_global_nutrition_context.py`.

These are mostly unit/service tests. They do not prove a deployed end-to-end chain across MAX → bot DB → backend API → prompt → LLM → delete/export. Backend tests prove its own API, not cross-repo erasure. No tests were executed during this audit; test names were inspected, not treated as live capability.

## 22. Actual vs Expected State Machine

### Actual code-reconstructed state machine

~~~mermaid
stateDiagram-v2
  [*] --> Incoming
  Incoming --> SessionOnly: no consent / no ayla link
  Incoming --> MemoryGate: global PERSONAL_DATA open
  MemoryGate --> MemoryCommand: show/forget phrase
  MemoryCommand --> LocalSoftDelete: forget one/all
  MemoryCommand --> SessionOnly: show response or no match
  MemoryGate --> Extract: ordinary free text
  Extract --> LocalDedup: GreenFactCandidate
  LocalDedup --> LocalActive: write MemoryEntry
  LocalActive --> Superseded: single-cardinality correction
  LocalActive --> BackendPatch: mappable candidate
  BackendPatch --> DeclaredProfile: best-effort PATCH
  Incoming --> PromptRead: linked + memory_green
  PromptRead --> Merge: local green + backend declared context
  Merge --> CoreRender: fixed fields, max 8, confidence softening
  CoreRender --> LLM: flattened prompt block
  LLM --> Conversation: stored assistant/user history
  LocalSoftDelete --> ReadEmpty: tombstone gate
  ReadEmpty --> [*]
~~~

### Expected KB model

~~~mermaid
stateDiagram-v2
  [*] --> Observation
  Observation --> Candidate
  Candidate --> PolicyCheck: sensitivity + data category + purpose
  PolicyCheck --> ConsentCheck
  ConsentCheck --> SessionOnly: missing/revoked consent
  ConsentCheck --> ProposalDecision: persistent candidate
  ProposalDecision --> ActiveMemory: approved + provenance + evidence
  ActiveMemory --> Retrieved: purpose-bound ContextEnvelope
  Retrieved --> ModelTransfer: minimal relevant facts
  ActiveMemory --> Superseded: correction/new fact
  ActiveMemory --> Expired: retention/TTL
  ActiveMemory --> Deleted: user forget/revoke/account delete
  Deleted --> NoPromptRead
~~~

### Diff

Actual has extraction and local supersession, but skips durable proposal/decision, purpose-bound consent, evidence-bound retrieval and unified deletion. Actual writes declared backend profile even when local bridge fails, and model transfer loses provenance/confidence metadata. Expected has one governed semantic-memory owner; actual has two semantic stores plus legacy reads.

## 23. KB → Code Divergence Matrix

| Capability | KB requirement | Actual implementation / evidence | Verdict | Severity |
|---|---|---|---|---:|
| Memory candidate | Proposal object and decision state before persistence (`Memory Domain Contract.md:182-219`) | extractor candidate only; no production proposal store/approval | NOT IMPLEMENTED | P1 |
| Consent gate | `preference_memory`, purpose and fail-closed resolution (`CSR.md:439-495,761-802`) | `PERSONAL_DATA`/`memory_green` legacy gates | MISMATCH | P0 |
| Provenance | user-stated vs confirmed inference, confidence/evidence (`Memory Domain Contract.md:221-239`) | source/provenance partial; confidence absent from DB; metadata discarded in prompt | PARTIAL | P0 |
| Persistence owner | W3 memory service via purpose-limited API (`Data Inventory Matrix.md:39-48`) | bot MemoryEntry plus backend profile mirror | MISMATCH | P0 |
| Retrieval | 12-step ContextEnvelope and authorization (`Context Resolution Contract.md:95-208`) | direct local reader + direct backend client + merge | PARTIAL | P0 |
| Relevance | purpose/category/sensitivity filtering | fixed green keys and max eight, no purpose tags enforcement | PARTIAL | P1 |
| Prompt rendering | model receives minimized approved envelope with provenance | flattened Russian strings; no provenance/timestamps/scope | PARTIAL | P1 |
| Correction | immutable supersession and audit | local explicit single-key supersession; backend partial LWW | PARTIAL | P1 |
| Forget one | delete fact and readback across mirrors | local soft-delete; backend clear skips price/favorite | PARTIAL | P0 |
| Forget all | cascade all persistent sources | local tombstone; backend clear not complete; conversations/analytics unresolved | PARTIAL | P0 |
| Export | memory/profile/derived context included | separate privacy machinery; complete cross-repo payload not proven | PARTIAL | P0 |
| Account delete | no active memory can re-enter prompt | local scrub/tombstone; prompt-source cascade unproven | PARTIAL | P0 |
| TTL | policy-specific lifecycle | green/backend fields effectively indefinite | MISMATCH | P1 |
| Goal memory | structured confirmed/editable goal | no production path found | NOT IMPLEMENTED | P1 |
| Diet memory | distinguish log/preference/observation/health | diet duplicated local/backend; no unified trust envelope | PARTIAL | P0 |
| Health safety | sensitive inference prohibited/off by default | nutrition gate strong; backend sensitivities separate prompt path | PARTIAL | P0 |
| Isolation | approved cross-provider purpose boundary | global UUID and informational tenant source; enforcement incomplete | PARTIAL | P0 |
| User visibility | see/correct/delete all used memory | chat and backend APIs separate; no unified view | PARTIAL | P1 |
| Recommendation use | memory facts used through governed context | prompt hints/tool selection; no decision evidence | PARTIAL | P1 |
| WHY | displayable reason tied to evidence | no recommendation/reason ID; LLM prose can create linkage | NOT IMPLEMENTED | P1 |

## 24. P0 Gaps

### P0-1 — Persistent memory is gated by a legacy broad consent, not the canonical purpose scope

**Impact:** a user can have persistent green facts written/read under `PERSONAL_DATA`/`memory_green` while KB requires explicit `preference_memory` purpose authorization and auditable proposal flow.

**KB:** `Consent Scope Registry.md:439-495,761-802,1065-1113`.

**Code:** `apps/consent/memory.py:47-55`; `apps/orchestrator/memory/personal_context.py:43-58`; `apps/identity/services/memory_writer.py:177-186`.

**Root cause:** pilot implementation predates canonical scope resolver.

**Minimum fix direction:** route persistent writes/reads through the approved scope/purpose decision boundary; no implementation performed.

### P0-2 — Two semantic stores and incomplete mirror deletion

**Impact:** “forget all” can leave backend price/favorite values or other profile data active, while local prompt state is empty.

**KB:** `Data Inventory Matrix.md:41-50,81-95`; `Memory Domain Contract.md:350-367`.

**Code:** `apps/orchestrator/memory/ayla_bridge.py:17-31,176-211`; backend `personal_context_views.py:182-241`.

**Root cause:** Variant-B pilot boundary and frozen backend contract lack clear all-fields erase semantics.

**Minimum fix direction:** define and verify one cross-repo erasure transaction/event/readback contract.

### P0-3 — Provenance is not carried to model or WHY decision

**Impact:** LLM can receive a derived backend field and a user-stated fact in the same display format; it can produce a personal explanation without evidence binding.

**KB:** `Memory Domain Contract.md:221-239`; `Consent Scope Registry.md:151-158`.

**Code:** `apps/orchestrator/memory_block.py:91-102,108-170`; `ayla-ai-core/src/ayla_ai_core/memory.py:96-170`; no confidence field per `apps/identity/tests/test_memory_entry_step35_write_compat.py::test_no_confidence_field`.

**Root cause:** adapter collapses structured facts into text; confidence is an ephemeral display parameter.

**Minimum fix direction:** preserve evidence/provenance in approved context envelope and prohibit unsupported WHY.

### P0-4 — Health/sensitivity data has divergent consumers

**Impact:** backend AI chat renders `skin_sensitivities` while MAX red-zone path excludes red/yellow; a single canonical safety boundary is not demonstrated.

**KB:** `Consent Scope Registry.md:138-159,1166-1204`; `Data Inventory Matrix.md:44-48`.

**Code:** `djangoproject-catalog/ai/personal_context_hint.py:107-117`; `ai-bot-platform/apps/identity/services/red_zone_reader.py`; `apps/orchestrator/nutrition_context.py:19-37`.

**Root cause:** backend declared profile and bot semantic memory are separate implementations.

**Minimum fix direction:** establish one sensitive-data policy and prove every consumer uses it.

### P0-5 — Global UUID memory is not intrinsically tenant-scoped

**Impact:** cross-provider reuse can expose provider-confidential facts if app-layer purpose boundary is bypassed.

**KB:** `Ayla Memory Domain Contract.md:314-349`; `ADR-0012 Dynamic User Model.md` draft must not override approved scope; `Data Inventory Matrix.md:63-70`.

**Code:** `MemoryEntry.source_tenant_id` help text says informational only (`apps/identity/models.py:768-775`); global managers at `:608-609,949`.

**Root cause:** cross-tenant user-owned memory design without one enforced purpose envelope.

**Minimum fix direction:** verify all reads against approved subject/provider/purpose relationship.

### P0-6 — Account deletion/export completeness is not proven across prompt sources

**Impact:** a deleted user’s conversation/profile/derived source could remain available to a prompt path even if local memory rows are tombstoned.

**KB:** `Consent Scope Registry.md:1235-1267`; `Memory Domain Contract.md:350-367`.

**Code:** `apps/identity/services/privacy.py:177-237,240-257`; backend `users/views.py:606-650`; no single cross-repo cascade evidence.

**Root cause:** multiple custodians and soft-delete retention.

**Minimum fix direction:** execute and verify a complete cascade/readback matrix.

### P0-7 — Sensitive derived profile is not visibly marked as derived in backend prompt

**Impact:** inferred `busy_days`/favorite/sensitivity fields may be interpreted by model as confirmed user statements.

**KB:** `Data Inventory Matrix.md:44-47,81-95`; `Consent Scope Registry.md:151-159`.

**Code:** `users/personal_context_inference.py:157-190`; `ai/personal_context_hint.py:59-127`.

**Root cause:** backend prompt formatter renders values, not provenance envelope.

**Minimum fix direction:** preserve and enforce source/provenance at model boundary.

## 25. P1 / P2 Gaps

P1: no production proposal approval; backend price/favorite correction incomplete; no unified “what Ayla knows” view; no verified TTL/staleness; no goal memory; no evidence-bound WHY; no cross-store atomic correction; no complete cache invalidation; no proven export of all derived/profile data; separate backend and MAX sensitivity behavior; inferred writer reachability unclear; backend `data_sources` not model-visible.

P2: legacy comments/schema fields not aligned with runtime semantics; duplicate prompt surfaces (`personal_context_block` and ai-core memory block); observability lacks a single memory-fact-used trace.

## 26. KB Conflicts / Stale Documents

1. `Data Inventory Matrix.md` is draft but contains normative-looking owner/write/consent rows; use as governance intent, not approved override.
2. Approved Memory Domain Contract/Consent Registry require proposal/purpose-bound persistence, while draft AMD-020 and runtime describe silent green writes. This is a KB conflict and implementation divergence.
3. `ADR-0012 Dynamic User Model.md` is draft/proposed; it cannot be used to declare proposal flow implemented.
4. Backend `AGENTS.md` says UserPersonalContext was “not implemented”, but branch code contains model/API/inference. This is repository-document staleness, not runtime evidence.
5. `ayla-ai-core` CHANGELOG memory block describes confidence-aware memory, but consumer loses durable provenance before calling it. Library capability is not proof of end-to-end contract.

## 27. Manual Verification Scenarios

No mutation was executed. Each scenario below requires a disposable test identity; items marked destructive must be approved separately.

| # | Scenario | Expected KB | Expected from code | Safe read-only check | Mutation? |
|---:|---|---|---|---|---|
| 1 | “Я предпочитаю массаж вечером” | candidate → consent/purpose → proposal | green explicit write + backend time PATCH | inspect trace and both stores | yes |
| 2 | Repeat same phrase three times | one idempotent fact | local dedup; bridge retries | compare IDs/counts | yes |
| 3 | “Теперь утром” | supersede old with provenance | local supersede; backend union may retain evening | read old/new and backend fields | yes |
| 4 | “Забудь, что люблю вечер” | delete fact + all mirrors | local field delete; backend time clear likely clears all slots | inspect readback | yes |
| 5 | “Что ты знаешь обо мне?” | complete user-visible memory inventory | local green chat view only or split backend view | invoke GET/read-only chat | no |
| 6 | “Что знаешь о моём рационе?” | category disclosure with source | diet field/local fact; nutrition logs not necessarily shown | inspect API payload shape | no |
| 7 | “Я не ем морепродукты” | diet fact/contraindication policy | extractor may drop unsupported excluded-food candidate | inspect extractor result | no |
| 8 | Correction of diet fact | supersede/clear with audit | named diet retraction bridges; excluded food not bridgeable | inspect code/test trace | yes |
| 9 | Food log then “у меня дефицит…” | no health inference without approved gate | nutrition aggregate feature off/default; health gate | config + consent matrix | no |
| 10 | Old diet vs new diet | explicit latest wins, old retained as superseded | local single key; backend LWW | compare both stores | yes |
| 11 | New conversation after 24h | only non-expired authorized facts | green/backend facts persist; no TTL default | new conversation read-only | no |
| 12 | Same person in another salon bot | approved reusable facts only | global UUID may reuse green context | inspect tenant/purpose trace | no |
| 13 | Global bot then salon bot | identity must resolve one person | BotUser shells link through Ayla UUID | inspect shell mapping | no |
| 14 | Delete one memory fact | readback excludes all representations | local soft-delete; bridge field-dependent | inspect after deletion | yes |
| 15 | Delete account | all active prompt sources gone | local privacy service + backend delete paths; cross-repo unknown | dry-run cascade inventory | yes |
| 16 | No consent | session allowed, persistence denied | `can_store_green_memory` returns false | unit/read-only test | no |
| 17 | Consent revoked | persistent read/write denied and deletion initiated | local gates; cross-store effect unknown | inspect consent rows/services | no |
| 18 | Sensitive input / allergy | special-category safety boundary | red/yellow writer guarded; backend sensitivity separate | inspect route and prompt renderer | no |
| 19 | Recommendation with preferred time | evidence-bound use | prompt hint/booking preference; ranking binding unknown | capture rendered prompt in test | no |
| 20 | WHY cites “ты любишь вечер” | reason must reference active fact | no recommendation_id/reason evidence; LLM may phrase it | inspect prompt/tool DTO | no |
| 21 | “Я не люблю массаж вечером” after old fact | correction should supersede, not duplicate | only extractor-recognized keys; otherwise new/none | inspect candidate parser | yes |
| 22 | Forget-all then repeat old fact | no re-learning until explicit re-enable policy | local tombstone blocks write; backend behavior separate | inspect local gate | yes |

## 28. Open Owner Decisions

Only unresolved questions not answerable from current code/approved KB:

1. Does Controlled Pilot activate `preference_memory` now, or is the broad `PERSONAL_DATA`/`memory_green` path an approved temporary exception? The KB currently does not reconcile this.
2. Is backend declared `UserPersonalContext` a permitted projection of W3 semantic memory, or an independent profile source for pilot? Current documents describe both boundaries.
3. Which fields are allowed to cross providers, especially diet, sensitivities, favorite masters and booking-derived signals?
4. What is the authoritative all-sources delete/export SLA and whether conversations/analytics remain retained or anonymized?
5. Should the recommendation/WHY path consume persistent memory in the pilot, and if so what evidence object is user-visible?

## 29. Ten Final Answers

1. **What Ayla remembers long-term:** explicit green conversational facts that pass consent (diet/time/district/price and selected supported keys), backend declared profile/preferences, conversation history, and derived profile/booking/nutrition data in their domains. Not all are “memory”.
2. **What is incorrectly conflated:** backend `busy_days`/favorite masters and RFM are derived; booking history is transactional; nutrition weekly aggregate is a projection; `diet_type` exists both as profile and memory mirror.
3. **Where persistent memory lives:** bot PostgreSQL `UserPersonalContext` + encrypted `MemoryEntry`, and separately backend PostgreSQL `users.UserPersonalContext`; conversations/profile are additional DB stores.
4. **Who writes:** bot extractor/writer for local explicit facts, backend API/mobile for declared fields, backend inference task for derived fields, bridge for mapped bot statements. LLM has no direct memory-write tool found.
5. **Is consent checked:** yes for local green and nutrition paths, but not in the canonical `preference_memory`/proposal/purpose form; therefore PARTIAL/MISMATCH.
6. **How it reaches a new conversation:** handler retrieves local current green facts and backend declared context, merges them, renders a text block, then passes it to concierge/core; conversation history is separately loaded.
7. **Does LLM see provenance:** only an ephemeral confidence softening for inferred local facts; no durable source, timestamp, evidence, consent scope or canonical provenance is passed. Backend derived fields are not visibly marked. No.
8. **Can the user see/correct/delete everything:** no unified proof. Chat and backend API support parts; price/favorite clearing and complete cross-store export/account-delete readback are not proven.
9. **What memory affects recommendation/WHY:** time/district/budget/diet/favorite values can enter prompt and time preference can affect booking continuation; a structured ranking/evidence/WHY binding is absent.
10. **Five most dangerous KB/runtime divergences:** legacy consent vs canonical purpose gate; two semantic stores with incomplete erase; provenance lost before model; divergent health/sensitivity consumers; global cross-tenant memory without a single enforced purpose envelope.

## 30. Handoff Status

- Overall verdict: **C — MULTIPLE COMPETING MEMORY SYSTEMS**.
- P0/P1/P2: **7 / 12 / 3**.
- Real semantic memory stores: **2** (`ai-bot-platform` bot memory and `beautygo_backend` declared context), plus conversation, derived profile, transactional and nutrition stores.
- Main privacy/safety gap: canonical purpose/proposal/provenance controls are not the controls that gate actual persistent memory and all sensitive consumers.
- Main runtime gap: split stores are merged best-effort and deletion/export is not proven end-to-end.
- VERIFIED: model schemas, local write/dedup/supersession, local consent/read/delete gates, backend profile API, bridge mappings, core rendering boundary, default nutrition flag and health consent gate.
- NEEDS LIVE READBACK: deployed store population, actual pilot feature flags, cross-repo deletion/export completion, cache contents, identity graph integrity, and exact user-visible WHY text.
- Owner decisions required: pilot consent mode, one/boundary source of truth, cross-provider field policy, deletion/export retention, and whether/how memory-backed recommendation/WHY is allowed.

