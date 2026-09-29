# Ayla Dialogue vs KB — forensic audit

Audit date: 2026-08-24  
Runtime: AndreyDeveloper84/ai-bot-platform@dev  
KB: AndreyDeveloper84/ayla-knowledge@main  
Method: read-only source tracing. No live booking, external mutation, code or KB changes.

Evidence labels: VERIFIED = supported by inspected code and/or canonical KB. INFERRED = deployment state or an unobserved external condition is required.

## 0. Executive verdict

| Area | Verdict | Severity | Evidence-backed summary |
|---|---|---:|---|
| C01 First Contact | PARTIAL | P0 conditional | Global C01 exists behind GLOBAL_BOT_ONBOARDING; when enabled it is need/outcome-first with free text and three goal-like chips. Default is false; deployment state is not proven. |
| C02 Clarification | PARTIAL | P1 | Concierge has ask_clarification, up to five options and free-text fallback. The structured resolver runs after the reply, not as the current action gate. |
| C03 Adaptive Context | PARTIAL | P1 | Session history, consent-gated personal context, memory and optional nutrition blocks reach Concierge. No single canonical context-resolution boundary is used. |
| C04 Recommendation + WHY | MISMATCH / BLOCKED | P0 | Runtime exposes catalog discovery and master cards. No separate Recommendation result, recommendation id, evidence-linked WHY or displayable reason contract was found. |
| C05 Execution | PARTIAL | P1 | Discovery → master → tenant booking handoff → service/date/slot → explicit confirmation → create is implemented. Recommendation → Service → Provider is not a separate runtime chain. |
| Recovery | PARTIAL | P1 | Safety, stale callbacks, no provider/slot, technical failures and handoff paths exist, but there is no unified canonical recovery state model. |
| Safety | SUPPORTED / PARTIAL | P0 | Deterministic inbound safety runs before discovery/LLM on both paths. Global red flags do not create AdminTask by design. |
| Memory | PARTIAL | P1 | Explicit green facts and personal context are stored/read under consent gates, through local helpers rather than the canonical context interface. |
| Goal | PARTIAL | P1 | Goal-like C01 chips are plain text inputs; a persisted Transformation Goal consumer was not verified. Nutrition goals are a separate path. |
| Mini App handoff | PARTIAL / CONDITIONAL | P1 | Native open_app and routes exist; URL fallback opens externally and still depends on initData validation. Effective deployment flags are unknown. |

Overall: the current bot is a working conversational/catalog/booking pilot, but it is not the KB-defined controlled discovery-and-recommendation journey. C01 and parts of C02/C03 exist; C04 as a distinct, explainable Recommendation layer is not implemented; C05 exists as a booking capability downstream of discovery.

## 1. Canon sources

The local ayla-knowledge checkout was dirty and on drf-1148/canon-under-version-control, so it was not treated as canonical. Evidence below comes from the clean main checkout ayla-knowledge-main.

| Source | Status/version | Relevant sections |
|---|---|---|
| 00 Foundation/Canon Governance/CANON_INDEX.md | draft, v0.2 | allowed statuses; registry entries |
| 00 Foundation/Canon Governance/OWNER_DECISION_REGISTER.md | draft, v0.3 | BOT-003 rulings; AYLA-DEC-0081 memory/context canonization |
| 01 Product/User Journeys/Ayla MVP User Journey Specification.md | approved, accepted, v1.2 | Stage 1–14; intent, clarification, context, recommendation, explanation, execution, recovery |
| 01 Product/BOT-001 First Contact Specification.md | approved, accepted, v1.0 | greeting/intent entry, free text, progressive collection, consent, Mini App boundary |
| 03 AI System/Ayla Intent Model Specification.md | approved, accepted, v1.0 | recognition, slots, confidence/clarification, safety, output contract |
| 03 AI System/Contracts/intent-output.schema.json | machine-readable contract | status, confidence, slots, evidence, clarification, safety |
| 05 Architecture/Ayla Context Resolution Contract.md | approved, accepted, v1.0 | interface and 12-step pipeline |
| 05 Architecture/Ayla Memory Domain Contract.md | approved, accepted, v1.0 | MemoryEntry, provenance, lifecycle, consent gates |
| 06 Safety and Governance/Consent Scope Registry.md | approved, v1.4 | fail-closed scope and consent lifecycle |
| 01 Product/BOT-003 Discovery and Recommendation Conversation Specification.md | draft, proposed, candidate, v0.1 | detailed C04 behavior; not canonical |
| 05 Architecture/Ayla MVP Recommendation Contract.md | draft, proposed, v0.4 | RecommendationResult/C03-C05 proposal; not canonical |
| 05 Architecture/ADR-0013 Recommendation Snapshot.md | draft, proposed, v0.1 | proposed snapshot; not runtime authority |
| 05 Architecture/ADR-0014 Conversation Context ID.md | draft, proposed, v0.1 | proposed context id; not runtime authority |

### KB gaps

1. The canonical Journey and Intent Model contain normative Recommendation/WHY stages, while the detailed BOT-003 and Recommendation Contract remain candidate/proposed. This is a KB governance gap, not automatically a runtime defect.
2. CANON_INDEX itself is draft while functioning as the registry. Its listed documents must still be checked by their own frontmatter.
3. The local non-main checkout contains uncommitted and untracked documents; it was excluded from canonical evidence.

Evidence: CANON_INDEX frontmatter and registry entries; BOT-003 frontmatter lines 5–8; Recommendation Contract frontmatter lines 5–8; Journey headings lines 540–760.

## 2. Actual runtime architecture

~~~mermaid
flowchart TD
  U[MAX Update] --> P[parse_max_webhook]
  P --> G[global or per-tenant handler]
  G --> I[BotUser + Conversation]
  I --> S[record user + short-term history]
  S --> SG[safety pre-check]
  SG -->|blocked| SR[canned safety reply]
  SG -->|allowed| R{priority routing}
  R --> OB[onboarding flag + WelcomeSkill]
  R --> CB[catalog/discovery/booking callbacks]
  R --> BC[booking continuation]
  R --> FP[fast path]
  R --> N[nutrition structured path]
  R --> C[Concierge LLM]
  C --> T[show_masters / show_salons / show_services / ask_clarification / start_booking]
  FP --> CARDS[discover_masters + master cards]
  T --> D[catalog or booking handoff]
  CARDS --> H[cb:discover:book]
  H --> B[tenant booking skill]
  B --> SL[service/date/slot]
  SL --> CF[preview + explicit confirmation]
  CF --> BK[authoritative create + revalidation]
  BK --> OK[booking success]
  C --> O[reply renderer]
  CARDS --> O
  SR --> O
  O --> X[record assistant + send_message]
~~~

| Node | Evidence | Actual behavior |
|---|---|---|
| Parse | apps/channels/max/parser.py; handler.py:102–107 | MAX payload becomes CanonicalEvent. |
| Global entry | handler.py:581–644 | Global BotUser/conversation; callback exceptions are separated from user text persistence. |
| Safety | handler.py:823–829, 1431–1469; orchestrator/safety/gate.py | Deterministic pre-check before LLM/discovery/photo. |
| Onboarding | handler.py:882–885; channels/max/global_onboarding.py:214–336 | Only global path; flag-gated; soft consent gate. |
| Fast path | handler.py:1006–1026; orchestrator/fast_path.py:319–388 | Deterministic service/master discovery. |
| Concierge | handler.py:1117–1168; orchestrator/turn_seam.py:95–196; concierge.py:1267–1648 | LLM chooses advertised tools. |
| Intent resolver | handler.py:1217–1235; intent_resolution.py:604–663 | Runs after response, best-effort and flag-gated. |
| Booking | orchestrator/handoff.py:188–535, 1086–1196; skills/booking | Tenant-scoped handoff, callback/typed continuation, slot and confirmation flow. |

## 3. Actual state machine

~~~mermaid
stateDiagram-v2
  [*] --> NEW
  NEW --> SAFETY_BLOCKED: safety fails
  NEW --> GLOBAL_ONBOARDING: flag on + needs_onboarding
  NEW --> ROUTE: flag off or existing flow
  GLOBAL_ONBOARDING --> CONSENT_PROMPT: start
  CONSENT_PROMPT --> FIRST_CONTACT: consent_yes
  CONSENT_PROMPT --> CONSENT_REFUSED: consent_refuse
  FIRST_CONTACT --> ROUTE: free text or quick action
  CONSENT_REFUSED --> ROUTE: soft gate
  ROUTE --> HUMAN_HANDOFF: handoff phrase
  ROUTE --> VISITS: visit callback or booking lookup
  ROUTE --> CATALOG: cb:catalog
  ROUTE --> BOOKING_CALLBACK: cb:book
  ROUTE --> BOOKING_CONTINUATION: active booking context + typed service/date
  ROUTE --> FAST_DISCOVERY: direct show-masters claim
  ROUTE --> NUTRITION: structured food/anketa/photo
  ROUTE --> CONCIERGE: other free text
  FAST_DISCOVERY --> MASTER_CARDS: catalog hit
  FAST_DISCOVERY --> CONCIERGE: no hit
  CONCIERGE --> CLARIFICATION: ask_clarification
  CONCIERGE --> MASTER_CARDS: show_masters
  CONCIERGE --> SALON_CARDS: show_salons
  CONCIERGE --> SERVICE_CARDS: show_services
  CONCIERGE --> BOOKING_HANDOFF: start_booking
  MASTER_CARDS --> BOOKING_HANDOFF: cb:discover:book
  BOOKING_HANDOFF --> SERVICE_SELECTION: service unresolved
  BOOKING_HANDOFF --> DATE_SELECTION: service resolved
  SERVICE_SELECTION --> DATE_SELECTION: service answer
  DATE_SELECTION --> SLOT_SELECTION: date/period
  SLOT_SELECTION --> CONFIRM_PREVIEW: slot selected
  CONFIRM_PREVIEW --> BOOKING_SUCCESS: explicit confirm + create succeeds
  CONFIRM_PREVIEW --> SLOT_CONFLICT: create recheck fails
  SLOT_CONFLICT --> SLOT_SELECTION: retry/alternative
~~~

Expected KB: C01 → consent when needed → semantic intent → targeted clarification → adaptive context → Recommendation + WHY → user decision → execution mapping → service/provider/slot/confirm/booking. Actual: safety/flags/callbacks/matchers/LLM → catalog/master cards → booking. Recommendation is absent as a distinct state.

## 4. C01 audit

- Global onboarding is controlled by GLOBAL_BOT_ONBOARDING, default false: config/settings/base.py:1472–1479. If enabled, handler.py:882–885 calls needs_onboarding/run_onboarding_turn.
- GLOBAL_WELCOME_TEXT/GLOBAL_S5_TEXT and consent behavior are in channels/max/global_onboarding.py:76–111 and 285–336. Consent is journaled with record_global_consent.
- C01 Quick Actions are three goal-like inputs: “Хочу выглядеть свежее”, “Беспокоят отёки”, “Хочу снять напряжение”; secondary “Найти услугу →” becomes “Какие услуги у вас есть”: channels/max/quick_actions.py:87–131.
- Free text is supported. Quick Action callbacks are converted to text by quick_actions.py:329–361 and handler.py:694–705.
- Global catalog/booking callbacks are not sent to the LLM or stored as raw user prose: handler.py:711–733, 886–905.

| Input | Actual route | Verdict |
|---|---|---|
| Хочу расслабиться | Free text → memory/booking checks → fast path only if service criteria match, otherwise Concierge | VERIFIED |
| Расслабляющий массаж | Named service can be claimed by direct show_masters and render master cards if catalog hits | VERIFIED |
| Мне постоянно зажата шея | Concierge with medical/safety boundary; no pre-action structured context state verified | VERIFIED |
| Хочу лучше выглядеть к отпуску | Concierge; no verified Transformation Goal persistence/confirmation | VERIFIED |
| Не знаю, чего хочу | Concierge ask_clarification or deterministic no-criteria question | VERIFIED |
| Найти услугу | Secondary action → “Какие услуги у вас есть” → possible show_services | VERIFIED |

C01 verdict: PARTIAL. It is closer to BOT-001 than the old marketplace welcome, but the global gate is optional/soft, the per-tenant welcome is separate, and deployment flag state is unknown. P0 conditional because the first user-visible state changes materially with one flag.

## 5. C02 audit

- ask_clarification is a Concierge tool with up to five options: orchestrator/discovery.py:241–260.
- Its renderer uses option text as callback payload; a tap re-enters as ordinary text and there is no pending-question server state: discovery.py:1027–1047.
- Criteria-less direct discovery asks for service and city: discovery.py:1067–1083.
- The intent resolver defines confidence, needs_clarification, clarification_reason, clarification_effect and evidence invariants: intent_resolution.py:128–205, 288–452.
- But the resolver is invoked only after the reply is delivered: handler.py:1217–1235. It cannot govern the current action.

Reachability:

| Flow | Classification | Evidence |
|---|---|---|
| Global WelcomeSkill consent | LIVE if flag enabled; default FEATURE FLAG OFF | base.py:1472–1479 |
| Per-tenant WelcomeSkill | LIVE BUT SECONDARY | handler.py:1393–1518 |
| GoalSelectScreen | ROUTE EXISTS; conversational reachability not proven | miniapp/src/App.tsx:1039–1044 |
| cb:welcome callbacks | LIVE | skills/welcome/tests/test_skill.py:344–365 |
| Raw quick/catalog/booking callback to LLM | DEAD on global path | handler.py:694–733, 886–905 |

Verdict: PARTIAL, P1.

## 6. C03 audit

| FACT | SOURCE | TRUST | USED IN DECISION? | SHOWN? | PERSISTED? |
|---|---|---|---|---|---|
| Current global conversation | short_term.recall + record_global_message; handler.py:671–733 | session | yes, prompt history | indirectly | Message + short-term |
| Explicit green facts | record_explicit_green_facts; handler.py:1237+ | consent-gated | yes | only when surfaced | memory/personal context |
| Personal context | render_current_personal_context; handler.py:1061–1071 | consent-gated | yes, prompt | indirectly | read model |
| AI-core memory block | build_concierge_memory_block; handler.py:1072–1082 | fail-closed | yes, prompt | indirectly | source memory |
| Nutrition picture | build_nutrition_context_block; handler.py:1083–1100 | health + consent + flag | only if enabled | indirectly | Ayla-backed |
| Active booking context | conversation.skill_state global_booking; booking_context.py:45–195 | transient, TTL 900s | yes | booking replies | conversation state |

Canonical Context Resolution requires resolve_context(..., purpose) and a 12-step pipeline: 05 Architecture/Ayla Context Resolution Contract.md:95–210. No such call was found in the inspected global route; multiple independent readers and prompt blocks are used. No general Context Sufficiency Check, correction state, or question counter was verified. Booking continuation is real but domain-specific.

Verdict: PARTIAL, P1.

## 7. C04 audit — especially WHY

Verdict: BLOCKED / MISMATCH.

WHAT authority: show_masters/discover_masters. Schema: orchestrator/discovery.py:112–163. Deterministic path: orchestrator/concierge.py:1649–1784.

WHY authority: none structured. Master cards are rendered with a generic heading, public card fields and callbacks: discovery.py:556–635. No recommendation_id, evidence list, reason code, context fact or snapshot is passed. The Concierge second pass phrases tool output as prose: concierge.py:1118–1196. No code binds such prose to a ranked recommendation decision.

No guard equivalent to “NO DISPLAYABLE WHY → NO RECOMMENDATION” was found. A non-empty card list is shown without a WHY record. Recommendation, Service and Provider are not separate runtime objects in this contour; the card callback carries tenant/master/service ids directly into handoff.py:188–329.

The runtime therefore implements catalog discovery/master enumeration, not the KB RecommendationResult. This is VERIFIED by code and P0 for product truth.

## 8. C05 audit

| Transition | Evidence | Verdict |
|---|---|---|
| Recommendation → execution mapping | No Recommendation object; master-card callback is mapping seam | MISMATCH |
| Service | handoff.py:118–186, 764–902 asks/resolves service | SUPPORTED, but after discovery |
| Provider/master | catalog master callback and tenant-scoped handoff | SUPPORTED |
| Slots | skills/booking/tools.py:704+ and callbacks/typed continuation | SUPPORTED |
| Explicit confirmation | confirm_booking is preview-only and emits two-button confirmation; tools.py:180–205, 471–527 | SUPPORTED |
| Authoritative write | execute_confirm/create_customer_booking; tools.py:1209–1344; booking/services/create.py:157–328 | SUPPORTED/PARTIAL |
| Stale/revalidation | slot_unavailable/master_archived/service checks; create.py:23–28, 269–328 | SUPPORTED |
| Duplicate protection | pending action and existing-booking idempotency checks | SUPPORTED |
| Slot conflict | slot_unavailable recovery | SUPPORTED |
| Choice after error | booking_context TTL 900s; API failures may escalate | PARTIAL |

The explicit confirmation gate is real. The divergence is upstream: discovery → master callback → booking, not Recommendation → Service → Provider → Slots.

## 9. Fast paths

| INPUT | MATCHER | OUTPUT | BYPASSES | KB compatibility |
|---|---|---|---|---|
| Хочу массаж / Расслабляющий массаж | fast_path.decide/claims_direct_show_masters | deterministic master cards on hit | Concierge and pre-action resolver | PARTIAL |
| Найди мне САЛОНЫ массажа | parser rejects direct claim | Concierge show_salons | direct path | SUPPORTED |
| No city/service criteria | has_discovery_criteria | clarifying question | unfiltered catalog | SUPPORTED |
| cb:catalog:* | callback prefix | catalog cards | LLM and history | SUPPORTED |
| cb:discover:book:* | callback prefix | booking handoff | C04 lifecycle | PARTIAL |
| cb:book:* | callback prefix | tenant booking skill | free-text Concierge | SUPPORTED |
| cb:visit:* | callback prefix | records/repeat booking | Concierge | SUPPORTED |
| cb:qa:* | resolve_tap_text | ordinary text pipeline | raw callback to LLM | SUPPORTED |
| food/water/anketa/photo | structured nutrition handler | nutrition result | general Concierge | SUPPORTED |
| goal-select | Mini App route | GoalSelectScreen | bot conversation | route exists; reachability unproven |

The requested conflict is implemented correctly at matcher level: “Хочу расслабиться” has no named service and goes to Concierge; “Расслабляющий массаж” may be direct discovery. Evidence: fast_path.py:319–388 and tests/orchestrator/test_fast_path_claim.py:195–301.

## 10. Goal / Context / Memory

- C01 chips are goal-like user-language examples, not a fixed seven-goal taxonomy: quick_actions.py:83–103.
- GoalSelectScreen exists, but is not evidence that conversational Goal participates in a recommendation: miniapp/src/App.tsx:1039–1044.
- No call from the inspected global conversation route was found that resolves/stores a Transformation Goal and feeds it into a Recommendation object.
- Nutrition goal handling is separate: handler.py:1028–1055; do not conflate it with Transformation Goal.
- Memory commands and explicit green fact writes are real and consent-gated: handler.py:920–946, 1056–1082, 1237–1245.
- “Что Ayla знает обо мне” affects prompts when surfaced, but no structured recommendation decision evidence is attached.

Verdict: Goal PARTIAL; Memory PARTIAL.

## 11. Safety / Consent

Safety evaluate_inbound runs before photo download, skill dispatch, discovery and Concierge on both paths: handler.py:823–829, 1431–1469. It returns canned text and records safety_pre_check. Global red flags intentionally do not create AdminTask: handler.py:769–777 and safety parity tests.

KB Intent Model requires blocked_safety, safety flags and no recommendation handoff: Intent Model.md:1344–1387. Runtime has an equivalent pre-check, but not the structured resolver output as current authority.

Global consent is behind GLOBAL_BOT_ONBOARDING, default false: base.py:1472–1479. When enabled, record_global_consent writes ConsentRecord and consent_at atomically: global_onboarding.py:325–336, 428–479. It is Variant A soft gate: discovery/one-off booking are not blocked; memory/proactive writes are separately gated.

## 12. Mini App handoff

- Native MAX web-app and external URL settings: config/settings/base.py:491–510.
- Welcome tests prove native open_app when MAX_BOT_WEB_APP exists, URL fallback when only MAX_MINIAPP_URL exists, and no Mini App buttons when both are empty: skills/welcome/tests/test_skill.py:107–166, 625–689.
- Route table: miniapp/src/lib/max-sdk.ts:63–145; App.tsx:1039–1078.
- initData HMAC/staleness gate: miniapp_api/views.py:125–170.

Native open_app is the supported in-client path. External fallback is verified in code, but whether it receives valid MAX initData is deployment/client-dependent; “fallback leads to 401” is INFERRED and was not live-tested.

## 13. Recovery

| Requested state | Actual branch | User result |
|---|---|---|
| NO_RECOMMENDATION | no-match or Concierge fallback | catalog/no-match or model fallback; no Recommendation state |
| SAFETY_BOUNDARY | evaluate_inbound false | canned safety reply |
| EXECUTION_UNAVAILABLE | Ayla/YClients/API error | mapped unavailable text or human handoff |
| NO_PROVIDER | no master/name match | explicit no-master text or clarification |
| NO_SLOTS | empty/unavailable schedule | unavailable/other-time/handoff |
| SLOT_CONFLICT | create recheck slot_unavailable | retry/alternative path |
| BOOKING_ERROR | BookingToolResult.error / should_handoff | mapped error or manager |
| NETWORK/TECHNICAL | Concierge outage/schedule failure | AI unavailable/technical fallback/retry |

Booking context preservation is real for global typed continuation, TTL 900s: booking_context.py:45–195. The canonical named states are not one runtime enum; recovery is branch-specific.

## 14. Divergence matrix

| Stage | KB requirement | Runtime | Evidence | Verdict | Severity |
|---|---|---|---|---|---:|
| Entry | welcome/category/free input; no premature profile | optional global onboarding; per-tenant welcome; free text | Journey:540–558; global_onboarding.py | PARTIAL | P0 conditional |
| Consent | scope-specific, fail-closed persistent operations | soft global gate; memory gates fail closed | CSR:87–107, 1065–1085; base.py | PARTIAL | P1 |
| Intent | intent/slots/confidence before recommendation | matcher/LLM acts; resolver runs after response | Journey:594–618; handler.py:1006–1026, 1217–1235 | MISMATCH | P0 |
| Clarification | targeted and minimal | ask_clarification up to five options; no unified pre-action consumer | Journey:620–644; discovery.py | PARTIAL | P1 |
| Adaptive context | canonical resolver, relevant/fresh/authorized | independent history/memory/prompt blocks | Context Contract:95–210; handler.py:906–1100 | PARTIAL | P1 |
| Recommendation | distinct result/record | master discovery/cards | Recommendation Contract:121–169, 1230–1313; discovery.py | NOT IMPLEMENTED | P0 |
| WHY | evidence-grounded displayable explanation | generic card text/model prose | Recommendation Contract:557–626; discovery.py; concierge.py | BLOCKED | P0 |
| Alternatives | alternative/no-recommendation semantics | no-match/catalog missing only | BOT-003:127–153; discovery.py | NOT IMPLEMENTED | P1 |
| Execution mapping | acceptance does not itself book | master callback enters booking | AYLA-DEC-0086; handoff.py | MISMATCH | P1 |
| Service/provider | separate entities | master output and callback combine discovery context | discovery.py:556–579 | PARTIAL | P1 |
| Slots/confirmation/write | fresh slot, explicit confirmation, authoritative write | show_slots, recheck, two-step confirm, idempotent write | booking tools/create | SUPPORTED | P2 |
| Recovery | named states and preserved control | branch-specific fallback/handoff | handler.py; handoff.py; booking | PARTIAL | P1 |
| Memory | MemoryEntry/provenance/context resolver | local memory helpers/prompt blocks | Memory Contract; handler.py | PARTIAL | P1 |
| Mini App | reachable native handoff with initData | native and URL fallback; flags unknown | base.py; miniapp_api; max-sdk | PARTIAL | P1 |

## 15. P0 gaps

### P0-1 — C04 Recommendation is not a separate decision

Problem: user-visible output is master/catalog discovery, not Recommendation.  
User impact: a list can look like a considered recommendation without stable identity or evidence.  
KB: Journey:671–707; Recommendation Contract:121–169, 557–626, 1230–1313.  
Code: discovery.py:112–163, 556–635, 1121–1190; concierge.py:1649–1784.  
Root cause: runtime was built around show_masters/catalog and handoff.  
Minimum fix direction: canonicalize C04 first, then define the approved boundary. No fix implemented.

### P0-2 — WHY has no structured authority

Problem: no reason code, evidence refs, recommendation snapshot or displayable WHY record.  
Impact: model prose can surround cards without proving why a specific option was selected.  
KB: Recommendation Contract §§12–13; Journey Stage 8.  
Code: discovery.py:556–635; concierge.py:1118–1196.  
Root cause: tool result is catalog DTO, not RecommendationResult.  
Minimum fix direction: owner/architecture decision on reason/evidence ownership. No fix implemented.

### P0-3 — Intent resolution does not govern the current action

Problem: resolver is post-response, best-effort and flag-gated.  
Impact: master discovery or booking handoff can happen before canonical intent status/evidence exists.  
KB: Intent Model recognition/confidence; Journey Stage 4.  
Code: handler.py:1006–1026 and 1217–1235.  
Root cause: resolver was added as post-response telemetry while operational routing remains matcher/LLM/tool driven.  
Minimum fix direction: owner decision whether this is intentional pilot scope. No code change.

### P0-4 — Global first contact/consent is deployment-conditional

Problem: GLOBAL_BOT_ONBOARDING defaults false; effective dev-bot environment is absent.  
Impact: new global user may bypass C01 welcome/consent if flag is off.  
KB: Journey Stages 1–3; Consent Scope Registry §6/§10.  
Code: base.py:1472–1479; handler.py:882–885.  
Root cause: explicit rollout flag with no checked-in deployment value.  
Minimum fix direction: verify deployed flag and owner-approved consent policy. No live system touched.

## 16. P1/P2 gaps

P1: no unified Context Resolution boundary; direct master-card acceptance bypasses distinct Recommendation → Service → Provider semantics; recovery has no canonical state enum; Mini App URL fallback is conditional on initData; GoalSelectScreen does not prove conversational Goal consumption.

P2: README still describes Sprint 0/empty apps while runtime contains mature paths; source has encoding/mojibake that reduces auditability; historical docstrings describe echo/legacy behavior beside newer live branches.

## 17. KB contradictions / stale docs

| Issue | Evidence | Classification |
|---|---|---|
| BOT-003 is candidate | frontmatter status draft/proposed/candidate v0.1 | KB GAP |
| Recommendation Contract is proposed | frontmatter status draft/proposed v0.4 | KB GAP |
| ADR-0013/0014 are proposed | frontmatter | non-authoritative |
| Approved Journey contains recommendation stages while detailed contract is non-canonical | Journey §§7–8; contract frontmatter | internal status gap |
| CANON_INDEX is draft | frontmatter | governance gap |

No KB file was edited.

## 18. Manual verification scenarios

No live mutation was executed. Booking scenarios require a sandbox or separate authorization.

1. New global user: observe first text, C01 buttons, consent journal, and whether discovery is already available.
2. “Хочу расслабиться”: observe Concierge vs direct cards, tool call and post-response resolver.
3. “Расслабляющий массаж”: observe fast-path claim, no-LLM metric and master cards.
4. “Мне постоянно зажата шея”: observe safety/medical boundary and whether any unsafe conclusion appears.
5. “Хочу лучше выглядеть к отпуску”: observe Goal handling, clarification and persistence.
6. “Не знаю, чего хочу”: observe question count, options and callback-to-text behavior.
7. Correct Ayla’s assumed fact: observe whether the question is suppressed and whether memory is changed.
8. No catalog match: observe no-match text and return path.
9. Safety red flag/diagnosis request: verify no cards/booking and action_type safety_pre_check.
10. Exact service name: observe service → masters shortcut.
11. Salon query: observe show_salons and city handling.
12. Master selection: observe cb:discover:book, tenant scope and service prompt.
13. No slots: observe whether master/service context survives and whether alternatives are offered.
14. Slot occupied before confirmation: verify revalidation, slot_unavailable, no duplicate write.
15. Booking success: verify explicit confirmation, authoritative create, idempotency and success text.
16. Stale catalog callback: verify stale-card reply and no raw callback in LLM history.
17. Quick Action tap: verify human phrase is persisted, not cb:qa payload.
18. “Что Ayla знает обо мне” / “Забудь всё”: verify consent-aware read/erase and no accidental re-add.
19. Food photo/nutrition request: verify health consent and no unsafe beauty crossover.
20. Mini App open: compare native open_app versus URL fallback, initData verification and actual route.

## 19. Open owner decisions

1. Is GLOBAL_BOT_ONBOARDING intended enabled for the current Controlled Pilot, and is Variant A soft-gate consent acceptable for foreign-LLM/intent-understanding operations?
2. Must approved Journey Recommendation/WHY stages be enforced before BOT-003 and Recommendation Contract are canonicalized, or is the pilot explicitly discovery-only?
3. Who owns C04 WHAT/WHY ranking and evidence: Ayla backend, bot-platform catalog, deterministic policy or future recommendation service?
4. Is post-response resolve_and_log_turn_intent intentionally telemetry-only, or must it become pre-action authority?
5. Is external Mini App URL fallback approved given require_init_data expects MAX-authenticated initData?

## 20. Five final answers

1. Ayla currently understands the user through deterministic callback/matcher paths first, then Concierge LLM tool selection. Structured intent resolution is post-response.
2. Recommendation-like selection happens in claims_direct_show_masters + discover_masters or LLM show_masters. No separate Recommendation authority exists.
3. WHY comes from generic card copy and/or free LLM prose after a tool result. Structured evidence-linked WHY is not implemented.
4. C01 is partial/flagged; C02 is tool-level clarification; C03 is partial history/memory/prompt assembly; C04 is blocked as a distinct Recommendation; C05 is real booking execution with confirmation/revalidation downstream of discovery.
5. Most expensive pilot divergences: catalog/master discovery presented as recommendation; no evidence-linked WHY; post-response intent resolver; unchecked soft-gated global C01/consent; master-card tap bypassing a distinct Recommendation → Service → Provider boundary.

### Handoff summary

- Overall verdict: functioning discovery/booking pilot, not end-to-end KB journey; C04 is the main blocker.
- Counts: P0 = 4 proven/conditional findings; P1 = 5; P2 = 3. KB-only governance gaps are separate.
- Main blocker: no distinct Recommendation + evidence-grounded WHY authority.
- VERIFIED: code paths, callback priority, safety pre-check, onboarding flag, Quick Actions, fast-path distinction, Concierge tools, booking confirmation/revalidation/idempotency, Mini App route/auth code, canonical statuses.
- INFERRED: effective dev-bot flags and deployed native open_app/global onboarding state; live wording on the deployed instance.
- Owner decisions needed: only the five questions in §19. No Linear task was changed or closed.
