# Ayla — Conversation State & Dynamic Quick Replies v1.1

**Status:** Working Canon — Decisions 1–14 reconciled  
**Scope:** MAX conversational surface → Recommendation (C04) → Mini App execution (C05)  
**Updated:** 2026-09-07  
**Supersedes:** `ayla-conversation-state-v1.md` as the current reconciled working canon.

---

## 1. Purpose

This document defines the product and runtime contract for Ayla's conversational decision loop: how a user moves from a need or direct booking request to a grounded recommendation and then into structured booking execution without losing context.

Ayla is **click-first, text-always-available**.

Core invariants:

- Buttons are dynamic projections of controlled semantic actions.
- Free text is always accepted and never requires pressing “Other” first.
- The LLM may interpret and render language, but does not own product policy, safety policy, catalog truth, availability, price, or booking truth.
- Ayla asks the next question only if the answer can change admissibility, ranking, or a required execution parameter.
- Memory, history, and Goals reduce unnecessary questions; they do not override current explicit intent.
- Recommendation, Goal, Service, Offer, Plan, BookingIntent, and Booking are separate objects.
- Persistent product state is created only through controlled promotion/persistence boundaries.

---

## 2. Canonical semantic separation

**Goal informs; Outcome anchors; Context differentiates; Policy decides.**

The following objects MUST NOT be collapsed:

- `CurrentNeed` — what the user wants in the current interaction.
- `Intent` — what the user is trying to do now, e.g. discovery or execution.
- `Goal` — a durable user-level desired outcome used for continuity over time.
- `Capability` — what a solution must be able to provide.
- `CanonicalService` — normalized Ayla service identity.
- `TenantOffer` — tenant-specific sellable implementation of a canonical service.
- `Provider` — master/specialist capable of executing an offer.
- `Recommendation` — Ayla's immutable decision in a concrete context.
- `Plan` — a versioned strategy for reaching a Goal over time.
- `PendingBookingIntent` — a cross-surface execution handoff carrying user constraints.
- `Booking` — authoritative transaction.
- `Reaction` — immediate user response to a recommendation.
- `Outcome` — downstream evidence such as booked, completed, positively evaluated.

A Goal belongs to the user, not to a salon. The current salon or marketplace determines which concrete offers can satisfy it now.

---

## 3. Conversation modes

Conversation mode is not a fixed flow. It may switch without losing already-confirmed context.

### 3.1 DISCOVERY

Used when the user does not know the exact service or wants Ayla to choose.

```text
CurrentNeed
→ Context
→ Safety
→ Candidate Resolution
→ DecisionReadiness
→ Recommendation
→ Reaction
→ C05 execution
```

Typical entry: **“Help me choose.”**

### 3.2 EXECUTION

Used when the user approximately or exactly knows what they want.

```text
Service Recognition
→ ambiguity/offer resolution
→ transaction constraints
→ availability
→ PendingBookingIntent
→ C05
```

Typical entry: **“Book.”**

### 3.3 Mode switching

- EXECUTION → DISCOVERY when the user says they do not know which service/variant to choose.
- DISCOVERY → EXECUTION when the user accepts or acts on a recommendation, or directly asks to book.
- Switching mode MUST preserve reliable already-known facts.

---

# Decision 1 — Session lifetime and handoff TTL

## 4. Runtime lifetime model

Three distinct lifetimes are canonical:

```text
1. ACTIVE CONVERSATION STATE
   inactivity TTL = 2 hours

2. RESUMABLE SESSION SUMMARY
   TTL = 24 hours

3. PENDING BOOKING INTENT
   handoff TTL = 30 minutes
```

### 4.1 Active ConversationState

- TTL is inactivity-based.
- Meaningful interaction resets the 2-hour timer.
- After expiry, full runtime state is discarded.
- ConversationState does not automatically become durable memory.

### 4.2 ResumeSummary

ResumeSummary is a minimal safe continuation summary, not a serialized ConversationState.

It may retain, when policy allows:

- last topic/current need;
- last recommendation reference;
- last reaction;
- enough context to offer `Continue` or `Start over`.

It MUST NOT be treated as authoritative transaction truth.

### 4.3 PendingBookingIntent TTL

- Handoff TTL is **30 minutes** from creation.
- TTL is **non-sliding**.
- After expiry, semantic intent may be used as resume context, but offer, price, provider, slot, availability, and booking policy MUST be resolved again.
- Final booking always revalidates transaction truth, regardless of intent age.

---

# Decision 2 — Runtime ownership and storage

## 5. Responsibility split

```text
ConversationState
→ runtime owner: ai-bot-platform
→ P0 storage: Redis
→ inactivity TTL: 2h

ayla-ai-core
→ interpretation + semantic decisions
→ receives State + Event + Context
→ returns StatePatch + StructuredDecision
→ does not mutate runtime storage

Ayla backend
→ authoritative domain truth

ai-bot-platform
→ orchestration + session state + channel presentation
```

The platform is an orchestrator, not the recommendation engine.

PendingBookingIntent is not owned exclusively by ConversationState because it must survive MAX → Mini App handoff.

---

# Decision 3 — Semantic contract ownership

## 6. Public contract boundaries

Canonical rule:

> **Canonical semantic conversation contracts belong to `ayla-ai-core`; runtime/channel contracts belong to `ai-bot-platform`; authoritative business/domain contracts belong to Ayla backend.**

Conceptually:

```text
ConversationSession
├── RuntimeEnvelope          ← ai-bot-platform
│   ├── session_id
│   ├── user_id
│   ├── channel
│   ├── channel_chat_id
│   ├── last_interaction_at
│   ├── expires_at
│   └── presentation state
└── SemanticState            ← ayla-ai-core contract
    ├── mode
    ├── current_need
    ├── delegation
    ├── observations
    ├── safety_signals
    ├── session_constraints
    ├── rejected_recommendations
    └── ...
```

Core-owned semantic API should include at minimum:

- `SemanticConversationState`
- `SemanticUserEvent`
- `StatePatch`
- `StructuredDecision`
- `SemanticOption`
- `Delegation`
- `Reaction`
- `SafetyResult`
- `RecommendationResult`

Additional rules:

- Platform normalizes raw MAX input into `SemanticUserEvent`.
- DomainContext is fetched fresh/relevantly and is not copied wholesale into semantic state.
- Semantic contracts are versioned.
- No duplicated enums across repositories.
- Unknown safety-critical semantics fail closed.
- MAX-specific fields do not belong in core.
- Authoritative Goal/Booking entities do not belong in core.
- Conversation flow does not belong in backend domain models.
- Prompts are not the sole behavioral contract.

---

# Decision 4 — Safety architecture

## 7. Safety is a mandatory cross-cutting gate

Canonical pipeline:

```text
User Event
   ↓
Signal Extraction
   ↓
Deterministic Safety Policy
   ↓
NORMAL / CLARIFY / CAUTION / STOP
   ↓
allowed / forbidden capabilities
   ↓
Recommendation / Execution
```

### 7.1 Safety states

- `NORMAL` — normal recommendation/execution is permitted.
- `CLARIFY` — recommendation/execution is paused until required safety clarification is resolved.
- `CAUTION` — continuation is allowed only within constrained policy space and wording.
- `STOP` — wellness recommendation/execution terminates according to safety policy.

### 7.2 Safety responsibilities

LLM may perform **signal extraction** from language, e.g. body area, after exertion, pain/discomfort, radiation to limb.

LLM MUST NOT decide safety policy.

Canonical ownership:

- executable Safety Engine → `ayla-ai-core`;
- canonical safety policy/rules → `ayla-knowledge`;
- hard domain/safety invariants override recommendation logic.

Small deterministic lexical/pattern detectors may supplement LLM extraction for critical signals.

A stronger positive detector may not be cancelled by a weaker negative inference. Explicit user correction may retract prior signals, but provenance of original and retraction must remain auditable.

Every material safety decision should be attributable to:

- `rule_id`;
- `policy_version`;
- extracted signals;
- provenance;
- resulting capability restrictions.

Safety is reevaluated after every meaningful user event, not only inside a special discomfort branch.

`delegation=HIGH` never bypasses `CLARIFY` or `STOP`.

At STOP:

- no diagnosis;
- no “book anyway” bypass;
- no wellness-service CTA that contradicts the blocking policy.

**Still open:** exact domain-specific safety signal matrix and exact production wording.

---

# Decision 5 — DecisionReadiness, not model confidence

## 8. Recommendation stopping mechanism

Ayla MUST NOT use LLM self-confidence as the runtime decision mechanism.

Canonical concept:

> **DecisionReadiness = deterministic assessment of whether confirmed evidence is sufficient to make the next recommendation decision.**

States:

```text
READY
NEEDS_DISCRIMINATION
NEEDS_REQUIRED_CONTEXT
INSUFFICIENT_EVIDENCE
BLOCKED
```

Rules:

- `READY` → recommend.
- `NEEDS_DISCRIMINATION` → ask one highest-value discriminating question.
- `NEEDS_DISCRIMINATION + delegation=HIGH` → MAY recommend.
- `NEEDS_REQUIRED_CONTEXT` → MUST ask.
- `INSUFFICIENT_EVIDENCE` → ask the broadest useful question.
- `BLOCKED` → recommendation prohibited; follow blocking policy.
- Next question is allowed only if its answer can change admissibility, ranking, or required execution parameters.
- High delegation cannot bypass required context or safety.
- `SURPRISE_ME` is a separate discovery semantic, not a generic override of insufficient evidence.

Every decision should carry `DecisionEvidence` / reason codes for WHY and auditability.

Numerical internal ranking scores may exist, but are not probability/confidence and are not a public semantic API.

---

# Decision 6 — Candidate Resolver and ranking

## 9. Staged candidate resolution

Canonical rule:

> **Ayla does not use one global ranking formula. Candidate resolution is staged: hard eligibility → semantic fit → transaction fit → contextual personalization → quality → controlled tie-break.**

### 9.1 Principles

- Hard constraints cannot be compensated by score.
- Current explicit intent outranks memory/history.
- Semantic fit outranks convenience.
- Availability optimizes execution but does not redefine need.
- Price strongly affects ranking only when price/budget is explicit or materially relevant.
- Budget may become a hard filter.
- Rating is a secondary quality/tie-break signal after relevance and execution viability.
- City/search scope is a filter, not an ordinary ranking score.
- Past experience is a contextual continuity boost only with strong provenance and current relevance.
- Platform revenue/economic benefit MUST NOT influence organic recommendation ranking.
- Alphabetical fallback is forbidden when top-N truncation could bias exposure.
- True ties use stable controlled exploration/exposure balancing, stable within same user/context but distributed across users/time.

### 9.2 Resolver separation

Do not use one giant service×provider score.

Prefer separate resolvers:

- Service Resolver
- Offer Resolver
- Provider Resolver
- Availability Resolver

### 9.3 Ownership

Backend owns authoritative filtering/truth:

- service/offer existence;
- active state;
- provider capability;
- availability;
- price;
- booking constraints.

`ayla-ai-core` owns semantic ranking/policy:

- need fit;
- contextual history relevance;
- reason codes;
- DecisionReadiness.

Platform orchestrates.

Ranking output should expose reason/provenance, not treat raw score as user-facing truth.

---

# Decision 7 — Recommendation Object & Lifecycle

## 10. Recommendation as an immutable product object

Canonical rule:

> **Recommendation is a standalone immutable cross-surface product object recording a concrete Ayla decision in a concrete context. Recommendation ≠ Service ≠ Offer ≠ Provider ≠ Booking.**

### 10.1 Core rules

- Every issued recommendation receives a unique `recommendation_id`.
- Authoritative persistence belongs to Ayla backend.
- `ayla-ai-core` creates `RecommendationDecision`, not authoritative storage.
- Recommendation stores:
  - primary candidate;
  - alternatives;
  - controlled context snapshot;
  - reason codes;
  - evidence/provenance references;
  - safety/policy/resolver versions.
- Do not copy the full conversation transcript into Recommendation.
- Recommendation is immutable after creation.

### 10.2 Alternatives and lineage

An alternative request creates a **new Recommendation** with:

- `parent_recommendation_id`;
- reason such as `ALTERNATIVE_REQUESTED`.

Example lineage:

```text
R1 → R2 → R3
```

### 10.3 Reactions vs outcomes

Immediate interactions/reactions may include:

- `WHY_REQUESTED`
- `ALTERNATIVE_REQUESTED`
- `ENGAGED`
- `REJECTED`
- `CONSTRAINT_ADDED`

Downstream outcomes may include:

- `NO_EXECUTION`
- `BOOKING_STARTED`
- `BOOKED`
- `COMPLETED`
- `CANCELLED`
- `POSITIVELY_EVALUATED`
- `NEGATIVELY_EVALUATED`

`Посмотреть варианты` = `ENGAGED`, not proven acceptance.

`Что ещё?` is a session-level alternative signal, not a durable dislike.

Booking attribution:

```text
Recommendation
→ PendingBookingIntent(recommendation_id)
→ Booking
```

If R1 → alternative → R2 → Booking, direct conversion is attributed to R2 while lineage may show R1 assisted the journey.

Recommendation does not reserve price/slot/provider. Final booking always revalidates transaction truth.

Canonical evidence invariant:

> `shown ≠ engaged ≠ booked ≠ completed ≠ liked`

---

# Decision 8 — Memory Promotion Policy

## 11. Durable memory is controlled promotion, never direct extraction

Canonical rule:

> **Conversation extraction never directly becomes durable memory. There is always an explicit Memory Promotion Policy between them.**

```text
USER EVENT
  ↓
Semantic Extraction
  ↓
ConversationState
  ↓
potential durable info?
  ↓
MemoryCandidate
  ↓
Promotion Policy
  ├── REJECT
  ├── SESSION_ONLY
  ├── REQUIRE_CONFIRMATION
  └── PROMOTE
        ↓
     UserMemory
```

### 11.1 Distinct stores/concepts

These are distinct:

- `ConversationState`
- History
- Goal
- authoritative domain truth
- `UserMemory`

### 11.2 Promotion rules

- LLM may create a `MemoryCandidate`, but never directly persist memory.
- Memory ontology is allowlisted; unknown classes fail closed.
- Situational facts default to `SESSION_ONLY`.
- Repeated behavior/history does not automatically become preference.
- Recommendation reactions are not durable memory.
- Explicit durable low-risk preference may be auto-promoted only if policy/consent permits.
- Ambiguous, inferred, or high-impact candidates require confirmation.
- Never auto-promote temporary symptoms, safety/red-flag observations, medical inference, one-off rejection, one-off booking pattern, negative streak inference, or mutable transaction truth.
- Health facts require separate HEALTH consent/data policy.
- Mutable domain facts remain in their authoritative entity, not generic memory.
- Goal remains a separate authoritative entity; memory may reference it but not duplicate it.

### 11.3 Provenance and scope

A durable memory fact conceptually needs:

- scope/domain;
- predicate/value;
- provenance;
- epistemic status;
- temporal validity;
- sensitivity/consent class.

Example:

```yaml
MemoryFact:
  id:
  subject.user_id:
  scope:
    domain:
    entity_type:
    entity_id:
  predicate:
  value:
  provenance:
    source_type:
    source_ref:
    captured_at:
  epistemic_status:
    explicit | confirmed
  lifecycle:
    valid_from:
    expires_at:
    status:
  policy:
    sensitivity_class:
    consent_scope:
```

Pattern-derived preference may become a candidate, but does not become memory via a magic frequency threshold.

Ownership:

- `ayla-ai-core` detects semantic `MemoryCandidate`;
- backend/memory domain owns consent, promotion, persistence, expiration, correction;
- `ayla-knowledge` owns canonical promotion policy.

New sessions retrieve only relevant memory subset, not the full user profile.

Canonical invariant:

> **Ayla may remember only what it is allowed to treat as durable; everything else may be known in current context/history without turning it into a claim about the person.**

---

# Decision 9 — Goal creation, promotion, and lifecycle

## 12. Goal model

Canonical rule:

> **Goal is a user-owned durable desired-outcome entity for continuity over time. `CurrentNeed`, Recommendation, Service, and Plan are not Goal.**

### 12.1 Goal creation

- `CurrentNeed ≠ Goal`.
- Core may extract a `GoalCandidate`, but cannot create authoritative Goal by itself.
- New Goal becomes `ACTIVE` only after an explicit user action.
- Usually Ayla first provides value, then offers continuity: “I can take this goal into account going forward.”
- If the user explicitly asks to create/follow a goal, creation may happen immediately through a controlled action.

### 12.2 Goal contents

Goal should retain:

- canonical outcome;
- original human wording (`user_wording`);
- target date/time horizon when relevant;
- provenance;
- lifecycle status.

Goal MUST NOT use salon/service/provider/slot as ownership semantics.

### 12.3 Lifecycle

Canonical statuses:

```text
ACTIVE
PAUSED
ACHIEVED
ARCHIVED
```

Freshness is a separate computed axis, e.g.:

```text
FRESH
NEEDS_RECONFIRMATION
```

Passing a target date does not imply `ACHIEVED`.

Ayla should not infer achievement only from indirect evidence such as completed procedures.

### 12.4 Multiple goals and relevance

- Multiple ACTIVE Goals are allowed.
- Only relevant Goal(s) enter a current decision through a Goal Relevance Resolver.
- Current explicit need outranks Active Goal.
- A current need that differs from an Active Goal does not modify the Goal.
- Goal status does not automatically change because of inactivity, a different booking, or a rejected recommendation.

### 12.5 Goal and proactive behavior

ACTIVE Goal is an eligibility input, not unconditional permission to message.

Proactive behavior still requires:

- consent;
- valid trigger;
- frequency policy;
- current relevance;
- safety.

### 12.6 Goal vs Plan

Goal describes the desired outcome. Plan is one proposed path and may change without changing Goal.

Authoritative Goal owner: Ayla backend.

Goal create/update/status commands should be idempotent.

Similar Goals are not merged automatically by LLM.

Canonical invariant:

> **Goal exists for continuity, not pressure: it shortens the path to relevant help but never overrides what the user explicitly wants now.**

---

# Decision 10 — Dynamic Quick Reply Contract

## 13. SemanticOption → channel presentation

Canonical rule:

> **Quick Reply is a presentation of a concrete `SemanticOption`, not standalone business logic and not a hard-coded dialog-tree transition.**

Pipeline:

```text
ayla-ai-core
↓
StructuredDecision
↓
SemanticOption[]
↓
ai-bot-platform
↓
MAX Presentation Adapter
↓
MAX buttons
```

Core does not know MAX-specific button APIs.

### 13.1 SemanticOption

Conceptual public contract:

```text
StructuredDecision
├── decision_id
├── based_on_state_revision
├── decision_type
├── prompt_semantics
├── options[]
└── reason_codes

SemanticOption
├── option_id
├── role
├── semantic_action
├── label_hint
├── presentation_priority
├── presentation_requirement
└── reason_code
```

Canonical roles:

```text
CHOICE
DELEGATE
ESCAPE
CONFIRM
ACTION
REACTION
CONSTRAINT_RESOLUTION
```

`label` is not the semantic value.

LLM may render approved semantics naturally but may not add new semantic options not supplied by controlled logic.

### 13.2 `I don't know` vs `Other`

- `I don't know / choose for me` = delegation signal, typically increasing `delegation`.
- `Other` = escape because offered options do not contain the user's answer.
- Free text is always valid, even if `Other` was not pressed.

### 13.3 Decision loop

A quick-reply click is normalized to a `SemanticUserEvent`; the system then recomputes state/safety/resolution/readiness.

Buttons do not encode a fixed tree such as `A → screen B`.

### 13.4 Stale interaction protection

ConversationState has monotonic `state_revision`.

Every StructuredDecision contains:

- `decision_id`;
- `based_on_state_revision`.

Stale callbacks are classified as:

```text
CURRENT
REVALIDATABLE
INVALID
```

Stale does not automatically mean invalid.

Transaction actions additionally revalidate backend domain truth.

Callbacks should contain an opaque reference such as `decision_id + option_id`, not authoritative price/slot/service business data.

Server validates ownership/admissibility of the option.

### 13.5 Presentation policy

- Prefer progressive disclosure, usually ~4–5 meaningful visible options.
- Semantic options may carry `presentation_priority`.
- Presentation requirement may be `REQUIRED / PREFERRED / OPTIONAL`.
- Escape/delegation options must not disappear accidentally during truncation.
- Confirmation is used only when ambiguity or safety/execution rules genuinely require it.
- Analytics keys off semantic IDs/actions, not localized labels.
- Semantic contracts are versioned.
- Unknown safety/execution semantics fail closed.

**Still open:** exact wire format and verified real MAX API/platform limits.

---

# Decision 11 — Catalog Semantic Mapping Contract

## 14. Four-level service model

Canonical model:

```text
Need / Outcome
      ↓
Capability
      ↓
CanonicalService
      ↓
TenantOffer
```

Definitions:

- `Need/Outcome` = what the user wants to achieve.
- `Capability` = what a suitable solution must provide.
- `CanonicalService` = normalized Ayla service identity.
- `TenantOffer` = concrete sellable tenant-specific implementation.

### 14.1 Mapping ownership

Canonical knowledge/catalog owns:

```text
CanonicalService ↔ Capability
```

Backend catalog domain owns:

```text
TenantOffer ↔ CanonicalService
```

Price, duration, active/bookable state belong to TenantOffer.

Offer naming alone does not prove Capability.

### 14.2 Mapping status

Canonical mapping status:

```text
VERIFIED
REVIEW_REQUIRED
UNMAPPED
```

Only VERIFIED mapping is ordinary recommendation truth.

LLM semantic inference by itself may generate a candidate mapping, but cannot make it VERIFIED.

### 14.3 Unmapped offers

An UNMAPPED offer may remain:

- visible in the tenant catalog;
- directly bookable when explicitly chosen by the user;

but MUST NOT be recommended based on unknown/unverified capabilities.

Therefore:

```text
catalog_visible ≠ recommendation_eligible
bookable ≠ recommendable
```

### 14.4 Recognition and execution

LLM may map natural language to existing canonical candidates, aliases, body areas, or service families. Runtime must not invent new ontology entities.

Unknown canonical semantics → clarify/review/fail closed rather than silently extending taxonomy.

If a user explicitly asks for a service unavailable in the current tenant, Ayla does not silently substitute another service. It may offer explicit recovery choices such as “find something similar here” or “search elsewhere.”

In recommendation mode, the system may choose among services satisfying the requested Capability because the user has not selected a concrete service yet.

### 14.5 Offer Resolution

If CanonicalService is known but multiple tenant variants exist, selecting duration/price/variant is Offer Resolution, not a return to discovery.

Example state progression:

```text
Need = KNOWN
Capability = KNOWN
CanonicalService = UNKNOWN
TenantOffer = UNKNOWN
```

then:

```text
CanonicalService = KNOWN
TenantOffer = UNKNOWN
```

then:

```text
TenantOffer = KNOWN
```

### 14.6 Scope

- Salon context resolves only against current tenant offers.
- Marketplace context may resolve across eligible tenants/providers.
- Goal stays user-owned and does not freeze to a specific tenant/offer.

Recommendation provenance should include used canonical IDs and catalog/mapping versions.

Canonical invariant:

> **AI may choose only between service capabilities that the catalog actually knows. Unknown data must remain unknown rather than be converted into a guess.**

---

# Decision 12 — Plan Recommendation Contract

## 15. Plan model

Canonical rule:

> **Plan is a controlled, validated strategy for achieving a Goal over time; it is not the Goal, not a Booking, and not a set of automatically created bookings.**

```text
Goal
↓
Plan
↓
PlanStep
↓
Recommendation
↓
PendingBookingIntent
↓
Booking
```

### 15.1 When Plan is appropriate

Plan is used only when multi-step coordination materially adds value, e.g.:

- multi-step outcome;
- time-bound Goal;
- dependent actions;
- explicit request for a plan.

A simple one-service need should remain a simple Recommendation.

### 15.2 Planning constraints

LLM does not invent:

- timing intervals;
- incompatibilities;
- sequencing requirements;
- recovery windows;
- safety planning rules.

Controlled planning rules belong in canonical knowledge/catalog.

Unknown critical planning constraint remains unknown and may lead to incomplete/blocked plan rather than invented precision.

### 15.3 Plan construction

Prefer:

```text
Goal / Outcome
→ required Capabilities
→ planning constraints
→ CanonicalServices / TenantOffers
```

A PlanStep may remain capability-level until execution.

It need not preselect service/provider/offer/slot prematurely.

### 15.4 Planning scope

Canonical values:

```text
GENERAL
TENANT
MARKETPLACE
```

Tenant-scoped Plan does not make Goal tenant-owned.

### 15.5 Versioning

Plan has durable identity with versioned `PlanRevision`.

A new revision is created only for material strategy changes such as step addition/removal/replacement, material timing change, Goal change, user rejection of a step, or strategy-impacting availability changes.

### 15.6 Plan validation

Conceptual result:

```text
VALID
INCOMPLETE
BLOCKED
```

`INCOMPLETE` is acceptable and preferable to false precision.

Time windows and sequencing claims require controlled provenance (`rule_id`, policy/catalog version, etc.).

### 15.7 Persistence and execution

Showing a Plan does not mean it is saved.

Saving a Plan does not mean Booking.

Durable Plan is created through a controlled persistence boundary, e.g. explicit “Save plan” / “Follow this plan”.

PlanStep execution evidence and outcome evidence remain separate.

Rejecting one step is not automatically durable memory.

Goal status changes may pause or invalidate active plan usage.

Active Plan is not unconditional proactive messaging permission.

Ownership:

- planning rules → `ayla-knowledge` / canonical catalog;
- plan composition/semantic validation → `ayla-ai-core`;
- durable Plan/PlanRevision/lifecycle → Ayla backend;
- conversational presentation → `ai-bot-platform`.

Canonical invariant:

> **Ayla may present only a plan for which every material step, restriction, and timing claim has a traceable source. If evidence is insufficient, the plan stays incomplete rather than becoming falsely precise.**

---

# Decision 13 — PendingBookingIntent / C05 Handoff Contract

## 16. Cross-surface booking handoff

Canonical rule:

> **PendingBookingIntent is a backend-owned cross-surface handoff object that preserves confirmed user intent between MAX and Mini App but never becomes the source of current transaction truth.**

### 16.1 Ownership and transport

Authoritative owner: Ayla backend.

Flow:

```text
ai-bot-platform
→ create PendingBookingIntent
→ Ayla backend
→ opaque intent_ref
→ MAX deep link
→ Mini App
→ authenticated load/revalidation
```

Deep link carries opaque handoff reference, not business truth or PII.

Possession of `intent_ref` is not authorization. Backend validates user ownership, purpose, expiry, and admissibility.

### 16.2 Constraint state

Primary states:

```text
KNOWN
UNKNOWN
FLEXIBLE
```

`null` must not collapse UNKNOWN and FLEXIBLE.

Example:

```yaml
provider:
  state: FLEXIBLE
```

means user does not require a particular provider.

### 16.3 User constraints vs resolved selection

Do not rewrite system selection as user preference.

Conceptually:

```yaml
user_constraints:
  service:
  provider:
  date:
  time:

resolved_selection:
  tenant_id:
  offer_id:
  provider_id:
  slot_id:
```

Example:

```text
user_constraints.provider = FLEXIBLE
resolved_selection.provider = Anna
```

This preserves provenance.

### 16.4 Provenance

Important constraint sources may include:

```text
USER_EXPLICIT
USER_CLICK
RECOMMENDATION
HISTORY_SHORTCUT
PLAN
SYSTEM_RESOLUTION
```

`entry_point` is required, e.g.:

```text
DIRECT_BOOKING
RECOMMENDATION
REPEAT_BOOKING
DEEP_LINK
PLAN_STEP
```

`recommendation_id` is present only when a real provenance chain exists.

Plan-originated handoff may carry plan/step references.

### 16.5 Booking scope

Use explicit booking scope rather than ambiguous tenant ownership:

```text
TENANT
MARKETPLACE
```

Tenant in PendingBookingIntent is transaction scope/context, not Goal ownership.

Tenant scope mismatch must not be silently accepted.

### 16.6 Handoff lifecycle

- Handoff TTL = 30 minutes from creation, non-sliding.
- First read/open does not destroy the intent.
- One intent represents one booking attempt.
- Successful Booking consumes the intent.
- Repeat booking creates a new intent.
- Starting Mini App execution within TTL may transition to an active execution phase; a user is not kicked out merely because total interaction time exceeds 30 minutes.

### 16.7 Mini App behavior

Mini App MUST NOT restart a fixed wizard.

Reliable `KNOWN` fields are not asked again unless transaction revalidation creates a conflict.

`FLEXIBLE` is also knowledge and does not require artificial questioning.

`UNKNOWN` is asked only if needed for execution.

### 16.8 Transaction revalidation

Before Booking, always revalidate authoritative truth, including as relevant:

- booking scope/tenant;
- offer active/existence;
- price;
- duration;
- provider capability;
- provider availability;
- slot;
- booking policy;
- payment/cancellation-relevant policy;
- required consent/domain constraints.

Material changes are never silently applied.

Explicitly requested service/provider/time is never silently substituted.

### 16.9 BookingIntent validation result

Conceptual result:

```text
VALID
NEEDS_RESOLUTION
MATERIAL_CHANGE
BLOCKED
```

This is distinct from `DecisionReadiness`.

Final Booking command must be idempotent.

Canonical invariant:

> **User intent survives the handoff; transaction truth is always revalidated.**

---

# Decision 14 — Analytics & Attribution Event Taxonomy

## 17. Analytics as evidence, not truth

Canonical rule:

> **Ayla Analytics is an append-only evidence layer for observable events and derived metrics. It is not a source of ConversationState, Memory, Recommendation, Goal, Plan, PendingBookingIntent, or Booking truth.**

### 17.1 Three event layers

```text
PRODUCT EVENTS
→ observable user/product journey facts

DECISION AUDIT
→ why Ayla made a decision

TECHNICAL TELEMETRY
→ how the system technically performed
```

These layers must remain conceptually distinct.

### 17.2 Event envelope

Conceptually:

```yaml
Event:
  event_id:
  event_type:
  occurred_at:
  received_at:

  actor:
    user_id:

  context:
    conversation_id:
    session_id:
    tenant_id:
    channel:

  correlation:
    decision_id:
    recommendation_id:
    booking_intent_id:
    booking_id:
    goal_id:
    plan_id:
    plan_step_id:

  payload:
    ...

  schema_version:
```

Event ingestion is idempotent by `event_id`.

`occurred_at` and ingestion time are distinct.

### 17.3 Product event backbone for P0

Recommended minimal backbone:

```text
conversation.started

decision.question_presented
interaction.quick_reply_selected
interaction.text_submitted

recommendation.created
recommendation.presented
recommendation.explanation_requested
recommendation.alternative_requested
recommendation.engaged

booking_intent.created
booking_intent.handoff_opened
booking_intent.validation_result
booking_intent.expired

booking.created
booking.cancelled
booking.completed

feedback.submitted
```

DecisionReadiness, CandidateResolver, and SafetyDecision belong primarily to audit streams.

### 17.4 Semantic analytics keys

Primary analytic dimensions use semantic IDs/actions, not localized UI text.

Examples:

- `question_semantic_id` rather than question copy;
- `option_id` / `semantic_action` rather than Russian button label.

Raw conversational text should not be copied into product analytics by default.

Sensitive safety/health evidence belongs to a protected audit/privacy contract, not a general analytics warehouse.

### 17.5 Recommendation exposure and lifecycle

`recommendation.created` and `recommendation.presented` are distinct.

Exposure/CTR denominators use `presented`, not `created`.

`Что ещё?` = `alternative_requested`, not automatic rejection.

Generic `recommendation.accepted` is not introduced in P0 because its semantics are too ambiguous.

Recommendation lineage is preserved via parent references.

### 17.6 Booking attribution

Strong direct attribution requires provenance chain:

```text
Recommendation
→ PendingBookingIntent(recommendation_id)
→ Booking
```

If R1 → alternative → R2 → Booking:

- direct attribution → R2;
- R1 may be assisted via lineage;
- Booking is counted once.

Direct/repeat booking without provenance is not attributed to an older recommendation merely because service names match.

For P0, do **not** use heuristic time-window attribution when explicit provenance is absent.

Plan-originated bookings may preserve both journey provenance (`PLAN_STEP`) and direct recommendation provenance when both exist.

### 17.7 Evidence ladder

Canonical evidence strength:

```text
presented
↓
engaged
↓
booked
↓
completed
↓
positively evaluated
```

Never jump stages.

Cancellation does not automatically mean recommendation failure.

Completed does not mean positive outcome.

Analytics cannot directly create Memory or mutate domain truth.

Economic metrics may be measured, but do not automatically become recommendation ranking factors.

Canonical invariant:

> **Every stronger Ayla product metric must be supported by a demonstrable chain of weaker facts, not an inference that the user “probably meant” something.**

---

## 18. Consolidated ConversationState v1.1

This is a conceptual semantic state, not a requirement to persist one JSON blob.

```yaml
SemanticConversationState:
  mode: DISCOVERY | EXECUTION

  current_need:
    domain:
    desired_outcome:
    body_area:
    occasion:
    time_horizon:
    observations: []

  active_goal_refs: []

  delegation:
    level: LOW | MEDIUM | HIGH
    source:

  safety:
    state: NORMAL | CLARIFY | CAUTION | STOP
    signals: []
    policy_version:

  session_constraints: []
  rejected_recommendations: []

  candidate_context:
    capability_candidates: []
    canonical_service_candidates: []

  decision_readiness:
    state: READY | NEEDS_DISCRIMINATION | NEEDS_REQUIRED_CONTEXT | INSUFFICIENT_EVIDENCE | BLOCKED
    reason_codes: []

  latest_recommendation_ref:
  latest_reaction:

  next_decision:
    decision_id:
    decision_type:
    reason_codes: []
```

Runtime/channel fields such as `session_id`, `channel`, `channel_chat_id`, `last_interaction_at`, `expires_at`, `state_revision`, and presentation state belong to `RuntimeEnvelope` in `ai-bot-platform` rather than the core semantic contract.

Authoritative Goal, Plan, Recommendation, PendingBookingIntent, and Booking entities are referenced by ID and remain in backend domain stores.

---

## 19. Canonical interaction behavior

### 19.1 Help me choose

For a new/unknown user, a working broad first semantic split remains:

- Relax
- Look better
- Recover
- There is discomfort
- I don’t know — help me

Exact copy is presentation/UX copy, not semantic canon.

For known users, relevant Goal/history may shorten the path but must preserve `Something else / Start over`.

### 19.2 I don’t know — help me

This increases delegation. Ayla should not respond with a catalog or require free text.

`Surprise me` is a high-delegation discovery semantic, not permission to bypass safety or required context.

### 19.3 Look better

Ayla asks about desired outcome/emphasis rather than proposing defects.

Example semantic areas:

- face/skin;
- body/figure;
- hair;
- hands/nails;
- event preparation.

A situational appearance need is not automatically a persistent Goal.

### 19.4 Relax

If one candidate clearly dominates and safety allows, recommend immediately.

Do not manufacture extra questions.

### 19.5 Discomfort

Direction is:

```text
user state
→ safety eligibility
→ desired outcome
→ available candidates
```

Never reverse this into a sales funnel designed to justify a predetermined service.

### 19.6 Direct booking

If the user knows the service, remain execution-first.

Natural language may fill multiple BookingIntent constraints at once.

Broad service ambiguity should be resolved only as far as needed.

If the user says “I don’t know which massage,” switch locally to discovery inside the already-known category rather than restart the whole journey.

### 19.7 Constraint resolution

When constraints conflict, Ayla proposes the minimal explicit relaxation rather than silently changing user intent.

Example:

```text
preferred provider unavailable tomorrow
→ keep provider, choose another date
→ keep date, choose another provider
```

---

## 20. Evidence priority

Historical evidence strength is not flattened:

```text
mentioned
→ considered
→ recommended
→ engaged/accepted-enough-to-execute
→ booked
→ completed
→ positively_evaluated
```

Runtime priority:

1. explicit current user statement;
2. current click;
3. current transaction/domain truth;
4. active relevant Goal;
5. history/memory.

Continuity is a shortcut, not a constraint.

---

## 21. LLM boundary

LLM MAY:

- parse free text into candidate semantic signals;
- understand synonyms and colloquial phrasing;
- render approved semantic options naturally;
- explain a grounded recommendation;
- summarize collected context;
- produce candidate Goal/Memory/Service mappings subject to controlled promotion/verification.

LLM MUST NOT independently:

- invent catalog availability, price, provider, slot, or offer truth;
- decide booking transaction truth;
- invent safety policy;
- diagnose;
- persist session facts as durable memory;
- create persistent Goals without controlled user action;
- claim past success without evidence;
- invent plan timing/compatibility rules;
- create authoritative catalog mappings;
- use self-confidence as recommendation readiness.

---

## 22. Implementation layer map

```text
MAX / Mini App
      ↓
ai-bot-platform
  RuntimeEnvelope
  Redis ConversationState
  channel normalization/presentation
      ↓
ayla-ai-core
  semantic extraction
  Safety Engine
  DecisionReadiness
  Candidate Resolver
  Recommendation / Plan semantics
  SemanticOption generation
      ↓
Ayla backend
  Goal
  Recommendation
  Plan / PlanRevision
  PendingBookingIntent
  Booking
  tenant catalog / offer / availability truth
      ↓
ayla-knowledge / canonical catalog
  safety policy
  memory promotion policy
  capability ontology
  canonical service ontology
  planning constraints
```

Analytics consumes evidence from all layers but does not become runtime/domain truth.

---

## 23. Reconciliation findings

The original v1.0 contained several provisional statements that are now superseded:

1. **10-minute session TTL** → superseded by canonical **2-hour inactivity TTL**.
2. **3-state safety (`NORMAL/CAUTION/STOP`)** → superseded by **4-state safety (`NORMAL/CLARIFY/CAUTION/STOP`)**.
3. **`HIGH/MEDIUM/LOW confidence` stopping policy** → superseded by deterministic **DecisionReadiness**.
4. **Single mixed ConversationState blob** → superseded by explicit split between `RuntimeEnvelope` (platform) and `SemanticConversationState` (core contract), with authoritative domain entities in backend.
5. **Quick replies as `{value,label}`** → superseded by `StructuredDecision + SemanticOption[]` with semantic roles, opaque callbacks, state revision, and stale interaction handling.
6. **Service/capability collapsed conceptually** → superseded by `Need/Outcome → Capability → CanonicalService → TenantOffer`.
7. **BookingIntent nested inside ConversationState** → superseded by backend-owned cross-surface `PendingBookingIntent` with KNOWN/UNKNOWN/FLEXIBLE, provenance, scope, and transaction revalidation.
8. **Plan as a recommendation type only** → expanded into separate controlled/versioned Plan object and PlanRecommendation/PlanDecision flow.
9. **Recommendation lifecycle loosely implied by status** → superseded by immutable Recommendation + event/evidence lineage.
10. **Memory eligibility described only informally** → superseded by explicit MemoryCandidate → Promotion Policy boundary.
11. **Goal persistence described only as situational vs durable** → superseded by GoalCandidate, explicit activation, lifecycle, freshness, and relevance rules.
12. **Analytics absent as contract** → added append-only evidence taxonomy and direct attribution via provenance chain.

No contradiction remains between Decisions 1–14 after applying the above replacements.

---

## 24. Remaining open items before canonical approval

The original 12-item open list is now largely closed. The remaining unresolved items are narrower and should be handled as separate follow-up decisions/workstreams:

1. **Exact safety signal matrix and production wording by domain.**  
   Architecture and states are fixed; exact red-flag/clarification rules and copy are not yet canonical.

2. **Verified MAX platform constraints and final wire format.**  
   Semantic Quick Reply contract is fixed; concrete callback payload limits, button limits, deep-link behavior, and channel quirks still need verification against the real MAX API.

3. **ResumeSummary authoritative storage/implementation detail.**  
   24-hour semantics are fixed; exact persistence owner/store can be finalized during implementation design.

4. **Human handoff / operator escalation in the conversation model.**  
   The behavior exists elsewhere in product decisions but is not yet represented as a first-class semantic state/decision in this canon.

5. **Concrete API/DB schemas.**  
   Semantic contracts are now fixed enough to design API endpoints, Pydantic/JSON schemas, DB tables, idempotency keys, and migration plan. Those are implementation artifacts, not unresolved product semantics.

---

## 25. Canon status after reconciliation

### Closed at semantic/product architecture level

- Session TTL and handoff TTL.
- ConversationState ownership.
- Semantic contract ownership/versioning.
- Safety architecture and states.
- DecisionReadiness.
- Candidate ranking architecture.
- Recommendation object/lifecycle/attribution chain.
- Memory promotion policy.
- Goal creation/lifecycle/relevance.
- Dynamic Quick Reply semantic contract.
- Catalog semantic mapping.
- Plan Recommendation contract.
- PendingBookingIntent/C05 handoff contract.
- Analytics & attribution event taxonomy.

### Not yet final production canon

The document remains **Working Canon** until the five remaining items in §24 are resolved or explicitly deferred with owner approval.

