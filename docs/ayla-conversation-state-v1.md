# Ayla — Conversation State & Dynamic Quick Replies v1.0

**Status:** Working Canon — owner decisions captured  
**Scope:** MAX conversational surface → Recommendation (C04) → Mini App execution (C05)  
**Date:** 2026-09-05

## 1. Product principle

Ayla is **click-first, text-always-available**.

- Contextual quick replies/buttons are generated for the current conversational state.
- Free text is a parallel input path, not a fallback that requires choosing “Other” first.
- Buttons are dynamic, but product semantics, allowed values, safety constraints, catalog truth, and transaction truth are controlled by deterministic application layers.
- The LLM may understand natural language and render natural language, but must not independently invent product policy, availability, safety eligibility, or transaction facts.
- The next question is asked only when its answer can materially change the recommendation, safety decision, or executable booking result.
- “I don’t know / choose for me” is a valid delegation signal, not an error.
- Memory should shorten the journey, never force continuation of a previous journey.

## 2. Core semantic separation

**Goal informs; Outcome anchors; Context differentiates; Policy decides.**

The following are distinct objects and MUST NOT be collapsed:

- `Goal` — durable user-level desired direction when it is genuinely persistent.
- `CurrentNeed` — what the user wants/needs in the current session.
- `Intent` — what the user is trying to do now (discover, get recommendation, book, inspect bookings, etc.).
- `Recommendation` — Ayla’s proposed solution for the current context.
- `Service` — canonical service/capability concept.
- `Offer` — tenant-specific sellable service variant (duration/price/etc.).
- `Provider` — master/specialist who can perform the offer.
- `BookingIntent` — current transaction constraints for execution.
- `Reaction` — user response to a recommendation.

A Goal belongs to the user, not to a salon. The available ways to achieve it belong to the current request/tenant/marketplace context. Tenant may be stored as immutable provenance or booking snapshot, but must not own or permanently constrain Goal resolution.

## 3. Conversation modes

Conversation mode is not a rigid flow; it is the current operating mode and can change without losing collected context.

### DISCOVERY
User knows a need/outcome but not necessarily the service. Typical entry: **“Help me choose.”**

`CurrentNeed → Context → Safety → Candidate Resolution → Recommendation → Reaction → C05`

### EXECUTION
User approximately or exactly knows what they want. Typical entry: **“Book.”**

`Service Recognition → Ambiguity Resolution → Offer/Constraints → Availability → BookingIntent → C05`

### Mode switching
- EXECUTION → DISCOVERY when the user says they do not know which service/variant to choose.
- DISCOVERY → EXECUTION immediately when the user accepts a recommendation or directly expresses a booking request.
- Switching mode MUST preserve already-known facts and constraints.

## 4. Unified ConversationState

Conceptual state (names may change in implementation):

```yaml
ConversationState:
  session_id:
  user_id:
  mode: DISCOVERY | EXECUTION

  current_need:
    domain:
    desired_outcome:
    body_area:
    occasion:
    time_horizon:
    observations: []
    certainty:

  active_goal_ref:

  intent:
    type:
    confidence:

  delegation:
    level: LOW | MEDIUM | HIGH
    source:

  safety_state:
    level: NORMAL | CAUTION | STOP
    signals: []
    evaluated_at:

  context:
    tenant_id:
    marketplace_scope:
    catalog_snapshot_ref:
    location:

  candidate_state:
    candidates: []
    ambiguity:
    required_discriminator:

  recommendation:
    recommendation_id:
    type: SERVICE | ALTERNATIVES | PLAN
    primary:
    alternatives: []
    why_facts: []
    status:

  reaction:
    type:
    reason:
    recommendation_id:

  session_constraints: []
  rejected_recommendations: []

  booking_intent:
    service:
    offer:
    tenant_snapshot:
    provider_preference:
    date:
    time_window:
    location:
    entry_point:
    recommendation_id:

  next_action:
    type: ASK | CONFIRM | RECOMMEND | EXPLAIN | EXECUTE | SAFETY_STOP
    quick_reply_set_id:
```

This is a conceptual model, not a requirement to store one JSON blob in a database.

## 5. Lifetime and persistence rules

### Session-only by default
These are transient decision state and MUST NOT automatically become persistent user memory:

- `CurrentNeed`
- current `Intent`
- `Delegation`
- `SafetyState` evaluation result (unless separately required for audit under an approved policy)
- candidate rankings/confidence
- `session_constraints`
- rejected recommendation list
- transient observations inferred from clicks/text
- temporary “I don’t know”/“choose for me” state
- temporary body area / mood / occasion unless separately promoted through an approved durable fact path

Session state should expire after a defined inactivity TTL; **10 minutes is a working UX assumption, not yet a canonical infrastructure TTL**. Transaction state that must survive Mini App handoff may require a longer explicit expiry.

### Persistable as domain/history records, not generic memory

- Recommendation event with `recommendation_id`, inputs/provenance, and reaction.
- BookingIntent when needed for MAX → Mini App handoff, with expiry.
- Booking transaction and booking snapshot.
- Completed service history.
- Explicit feedback/review.
- Goal lifecycle.

These should live in their authoritative domain stores and be retrieved as context; they should not be duplicated as free-form memory.

### Eligible for durable user memory only under approved memory rules

Examples:
- explicit stable preference: “I prefer gentler massage”;
- durable relevant fact the user intentionally supplied and policy permits;
- active long-term Goal where persistence is appropriate.

A visit-specific statement such as “today it was too intense” MUST NOT automatically become a permanent preference.

### Forbidden automatic memory promotion

Do NOT turn these into durable memory merely because the model inferred them:

- diagnosis or medical/health inference;
- “user dislikes X” from a single rejected recommendation;
- persistent body problem from a current-session symptom;
- persistent preference from one visit-specific reaction;
- Goal inferred from a situational request such as “I want to relax today”;
- tenant ownership of a Goal;
- availability/price/slot facts;
- model-generated explanations as facts about the user.

## 6. Dynamic quick reply contract

Quick replies are contextual UI projections of allowed semantic actions.

The system should produce a structure conceptually like:

```json
{
  "prompt_key": "recovery.source",
  "options": [
    {"value": "after_training", "label": "After training"},
    {"value": "heavy_week", "label": "After a hard week"},
    {"value": "body_tension", "label": "There is body tension"},
    {"value": "general_fatigue", "label": "Just tired"},
    {"value": "unknown_delegate", "label": "I don't know — choose for me"}
  ]
}
```

Labels may be localized/personalized within approved semantics. The underlying values and allowed actions are controlled.

### Button roles

1. **Selection** — provides a semantic value: Relax / Recover / Look better.
2. **Confirmation** — confirms interpretation: Yes / Not quite.
3. **Action** — moves forward: View options / Book.
4. **Reaction** — Why? / Another option.
5. **Constraint resolution** — Other master / Other day.
6. **Delegation** — I don’t know / Choose for me.
7. **Escape** — Other / I’ll describe it myself.

`Other` and `I don’t know` are different:
- `I don’t know` = user accepts the question but delegates the answer/decision.
- `Other` = offered semantic space does not contain the user’s answer.

## 7. Question policy

Before asking a question, the orchestrator must evaluate:

1. Is the answer already known from the current message/click?
2. Is it safely reusable from relevant memory/history?
3. Does the answer change candidate ranking, safety state, or booking execution?
4. Can a reasonable recommendation be made now given the user’s delegation level?

If the answer will not change the decision, the question should not be asked.

Progressive disclosure applies: show 4–5 meaningful options rather than exposing a large internal taxonomy. Expand only when necessary.

## 8. Recommendation stopping policy

Conceptually:

- `HIGH confidence` → recommend.
- `MEDIUM confidence` + high delegation → recommend and make alternatives easy.
- `MEDIUM confidence` + low/medium delegation → ask one high-value discriminator.
- `LOW confidence` → ask.
- `SAFETY STOP` → recommendation prohibited.

Confidence MUST NOT be an ungrounded LLM self-score. It should derive from controlled evidence: candidate separation, required context completeness, conflicting signals, safety uncertainty, and user delegation.

## 9. Safety boundary

Safety is a cross-cutting gate evaluated after every material state update, not a separate fixed questionnaire.

Conceptual levels:

- `NORMAL` — normal wellness/beauty recommendation allowed.
- `CAUTION` — restricted/non-diagnostic wording; may show appropriate wellness options only under policy.
- `STOP` — service recommendation is stopped; do not offer a bypass such as “recommend massage anyway.”

Ayla must not diagnose. User-reported pain, trauma, numbness/weakness, unexpected symptoms, etc. can trigger controlled safety questions/rules. Negative answers to selected red flags do **not** prove that a service is medically safe.

Ayla should frame its own offered options through desired outcomes rather than introducing negative body judgments. If the user uses negative/problem language, Ayla may understand it without amplifying it into unsupported diagnosis or durable memory.

## 10. Help me choose — entry behavior

For a new/unknown user, a working first-level pattern is:

- Relax
- Look better
- Recover
- There is discomfort
- I don’t know — help me

Exact copy remains UX-copy, not semantic canon.

For a known user, memory/history may create a shortcut such as “Continue recovery after training?” but must always preserve an easy “Something else / Start fresh” route.

Memory may reorder or shortcut choices; it must not assert a current intention that the user has not expressed.

## 11. “I don’t know — help me”

This means increased delegation. Ayla should not respond with a catalog or demand a free-text formulation.

A useful approach is to ask an easy state/outcome question, e.g. tired / tense / want to feel more put together / everything is fine, want something pleasant.

“Surprise me” can be a high-delegation discovery action. A request for another surprise is a rejection/alternative signal for the current recommendation, not evidence of a permanent dislike.

## 12. Look better branch

Ayla asks about desired emphasis/outcome, not “what is wrong with you.” Example semantic areas:

- face/skin
- body/figure
- hair
- hands/nails
- event preparation

Then desired outcome. Avoid proactively framing choices as defects such as “remove fat,” “fix figure,” etc. User-provided language may still be understood.

Event preparation introduces `occasion` and `time_horizon`, which may materially change recommendations.

A Recommendation may be:
- one service;
- primary + alternatives;
- a multi-step plan.

A plan must be grounded in actual catalog capabilities, constraints, compatibility, safety, and current context; it must not be invented by the LLM as an unconstrained beauty plan.

A situational wish is not automatically a persistent Goal. Long-term goals may be offered for continuity when persistence is meaningful.

## 13. Relax branch

If the need is clear and one available candidate clearly dominates, Ayla should recommend immediately rather than conduct an interview.

If several candidates remain materially different, ask the discriminator that changes the choice. “Choose for me” lowers the threshold for making a reasonable primary recommendation while keeping alternatives accessible.

Default output is one primary recommendation, not a catalog list. Primary recommendation does not mean only possible recommendation.

## 14. Discomfort branch

This is a safety stress case.

The user may identify area/state through click-first options. When pain/trauma/other controlled signals appear, the safety layer may interrupt the normal recommendation loop.

Do not build a sales funnel that asks questions designed to lead to a predetermined service. Direction is:

`user state → safety eligibility → desired outcome → available candidates`

not:

`desired service → questions that justify selling it`.

## 15. Continuity and evidence strength

Historical evidence has different strength and MUST NOT be flattened:

`mentioned → considered → recommended → accepted → booked → completed → positively evaluated`

Examples:
- recommended but never booked ≠ user preference;
- booked but no feedback ≠ “it worked for you”;
- explicit positive feedback is stronger evidence;
- one visit-specific negative reaction ≠ permanent dislike.

Current explicit intent has priority over historical context. Working priority:

1. explicit current user statement;
2. current click/selection;
3. current transaction/context truth;
4. active relevant Goal;
5. historical memory.

Continuity should offer a shortcut, not force continuation.

If a prior successful service is unavailable in the current tenant, reuse the semantic need/capability and resolve against the current tenant catalog rather than carrying the old tenant service ID as the solution.

## 16. Direct booking / “Book”

Direct booking is execution-first. Do not ask recommendation questions when the user already knows the service.

If the user says a broad/ambiguous service (“massage”), clarify only the unresolved semantic dimension. If they say “I don’t know which massage,” locally switch to DISCOVERY within the already-known category.

Natural-language input can populate several BookingIntent constraints at once, e.g. service + date + time window + provider. Do not ask again for values already supplied.

Semantic service recognition should resolve:

`user phrase → canonical service/capability → current tenant Offer(s)`

When confidence is insufficient, confirm the interpretation.

## 17. BookingIntent and execution

Conceptual fields:

```yaml
BookingIntent:
  service:
  offer:
  tenant_snapshot:
  provider_preference:
  date:
  time_window:
  location:
  entry_point:
  recommendation_id:
```

A field may be known, unknown, or flexible.

`tenant_snapshot` is transaction context, not Goal ownership.

`entry_point` is useful provenance (direct booking, recommendation, repeat, deep link, etc.).

`recommendation_id` preserves attribution when booking follows a recommendation.

“Repeat” does not automatically create a booking. Current price, offer, provider availability, slots, and booking conditions must be resolved from transaction truth.

## 18. Constraint resolution

When exact booking constraints cannot all be satisfied, Ayla should identify the minimal useful relaxation rather than return a dead end.

Example: preferred provider unavailable tomorrow evening:

- keep provider → another date;
- keep date/time → another provider.

Do not silently relax constraints.

## 19. MAX ↔ Mini App handoff

MAX and Mini App are one journey.

MAX is strongest for:
- understanding need;
- narrowing ambiguity;
- recommendation;
- simple confirmations;
- collecting a small number of constraints.

Mini App is strongest for:
- comparing many masters/slots/offers;
- structured booking execution;
- current transactional truth.

C05 should receive the already-collected BookingIntent. It MUST NOT ask the user to reselect service/date/time/provider constraints that MAX already reliably captured.

Conversation narrows; structured UI compares and executes.

## 20. LLM boundary

LLM may:
- parse free text into candidate structured signals;
- understand synonyms and colloquial service descriptions;
- render approved semantic options naturally;
- explain a grounded recommendation;
- summarize collected context.

LLM must not independently:
- invent catalog availability, prices, providers, or slots;
- decide booking transaction truth;
- invent safety policy;
- diagnose;
- promote inferred session facts into durable memory;
- invent persistent Goals;
- claim a past service “worked” without supporting evidence;
- create unconstrained plans not validated by domain rules/catalog.

## 21. Implementation layers

Recommended responsibility split:

```text
MAX / Mini App UI
      ↓
Conversation Orchestrator
      ↓
State Extractor / Intent Recognition
      ↓
Safety Policy Gate
      ↓
Context + Memory Retrieval
      ↓
Catalog / Candidate Resolver
      ↓
Recommendation Policy
      ↓
Quick Reply Builder
      ↓
Booking Intent Resolver
      ↓
Availability / Booking Domain
```

The LLM participates in extraction/rendering/explanation, but controlled domain services own policy and truth.

## 22. Decisions captured

1. Dynamic contextual buttons: **YES**.
2. Arbitrary LLM-generated product semantics: **NO**.
3. Click-first + text always available: **YES**.
4. Fixed giant decision tree: **NO**.
5. Ask only decision-changing questions: **YES**.
6. Memory shortens path but cannot force prior intent: **YES**.
7. “I don’t know” is delegation, distinct from “Other”: **YES**.
8. One primary recommendation by default; alternatives on demand/rejection: **YES**.
9. Safety is cross-cutting and may interrupt any branch: **YES**.
10. Current explicit intent outranks history: **YES**.
11. Session reaction/rejection does not automatically become durable preference: **YES**.
12. Situational need does not automatically become persistent Goal: **YES**.
13. Goal is user-level; tenant is current resolution context: **YES**.
14. Direct booking bypasses unnecessary recommendation: **YES**.
15. Conversation mode may switch DISCOVERY ↔ EXECUTION without losing state: **YES**.
16. Service recognition resolves user language → canonical capability → tenant offer: **YES**.
17. Repeat must re-resolve current transaction truth: **YES**.
18. MAX passes collected BookingIntent to C05; Mini App must not restart the journey: **YES**.
19. `entry_point` and `recommendation_id` are useful BookingIntent provenance: **YES**.
20. Tenant in BookingIntent is transaction snapshot/context, not Goal ownership: **YES**.

## 23. Open items before canonical approval

- Exact session inactivity TTL and BookingIntent handoff TTL.
- Exact schema/owner of ConversationState: platform vs core vs consumer.
- Canonical vocabulary for `CurrentNeed`, `Outcome`, `Delegation`, `Reaction`, and `SafetyState`.
- Deterministic confidence/evidence model and stopping thresholds.
- Approved safety signal matrix and wording by domain.
- Memory promotion policy/API and provenance requirements.
- Goal creation/promotion UX and consent semantics.
- Quick reply API schema and MAX platform limitations.
- Catalog semantic mapping contract: canonical Service/Capability ↔ tenant Offer.
- Multi-step Plan Recommendation representation and validation.
- Exact C05 PendingBookingIntent API contract after tenant semantics reconciliation.
- Analytics event taxonomy for question → click/text → recommendation → reaction → booking attribution.

