Ты работаешь над проектом Ayla.

ТВОЙ ТРЕК:
CONTROLLED PILOT READINESS — единая матрица готовности Ayla к первым реальным пользователям.

Цель:
получить один доказательный документ, который отвечает на вопрос:

    "Что конкретно осталось сделать
     до запуска Controlled Pilot на первых 5–10 реальных пользователях?"

Это НЕ архитектурный дизайн-трек.
Это НЕ implementation-трек.
Это READINESS / RECONCILIATION / RELEASE-GATE measurement.

==================================================
0. РЕЖИМ РАБОТЫ
==================================================

Сначала только measurement + reconciliation.

НЕ писать production code.
НЕ делать migrations.
НЕ исправлять bugs.
НЕ менять prompts.
НЕ создавать новые product requirements.
НЕ переоткрывать уже закрытые owner decisions.
НЕ создавать implementation PR.
НЕ обновлять Linear без отдельной команды.
НЕ начинать full Plan Engine.

Если видишь дефект:
- документируй;
- классифицируй;
- укажи blocker / degraded / post-pilot;
- не исправляй.

После отчёта STOP.

==================================================
1. ЧТО МЫ НАЗЫВАЕМ CONTROLLED PILOT
==================================================

Controlled Pilot = настоящая Ayla на ограниченном production-like контуре:

- реальные пользователи;
- реальный MAX;
- реальный пилотный каталог;
- реальные мастера;
- реальное расписание;
- реальные Booking;
- реальные cancellation/reschedule;
- ограниченный Product Brain;
- controlled rollout 5–10 пользователей.

Это НЕ demo.

Pilot не обязан поддерживать весь BeautyGO.

Главное:
несколько ключевых сценариев должны работать безопасно,
детерминированно и end-to-end.

==================================================
2. PILOT SCENARIOS
==================================================

Обязательно оценить готовность минимум этих сценариев.

P1 — direct service execution

User:
    "Хочу классический массаж"

Expected:
    service recognition
    → Safety if applicable
    → current offer/provider/availability
    → Booking
    → My Bookings

P2 — discovery / recommendation

User:
    "Хочу расслабиться"

Expected:
    CurrentNeed
    → DecisionReadiness
    → максимум нужный question
    → Safety
    → Capability
    → CanonicalService
    → VERIFIED TenantOffer
    → Recommendation
    → Booking

P3 — durable Goal

User:
    "Хочу регулярно меньше уставать"

Expected:
    GoalCandidate
    → controlled promotion
    → Goal
    → later continuity
    → Recommendation

P4 — complex Goal / Plan Lite

User:
    "Через два месяца свадьба,
     хочу подготовиться"

Expected:
    Goal / event context
    → PlanEligibility
    → capability-level Plan Lite if grounded
    → UNKNOWN where no planning rule exists
    → no invented dates/frequency/sequence

P5 — Safety clarification

User input requires:
    CLARIFY

Expected:
    SafetyResult
    → priority safety question
    → answer ledger
    → recompute
    → no uncontrolled re-ask

P6 — Safety STOP

Expected:
    affected capability blocked
    → no recommendation/booking bypass

P7 — no VERIFIED recommendation candidate

Expected:
    no fabricated recommendation
    → controlled direct-browse / self-selection fallback

P8 — stale transaction state

Provider/slot/offer changes between recommendation and booking.

Expected:
    fresh revalidation
    → no silent substitution

P9 — free text vs quick reply

Same semantic answer by tap and text.

Expected:
    same semantic outcome
    → provenance preserved

P10 — repeat question regression

Resolved semantic question must not reappear
without controlled reason.

==================================================
3. RELEASE POLICY: STOP vs DEGRADED
==================================================

Каждую capability классифицировать:

A. STOP PILOT
Если capability не готова, запуск запрещён.

B. DEGRADED BUT ALLOWED
Capability можно отключить feature flag,
а pilot продолжить.

C. POST-PILOT
Не требуется для первых 5–10 пользователей.

По принятой owner-модели:

STOP-кандидаты минимум:

- identity/auth;
- privacy/data boundaries;
- Safety;
- booking integrity;
- transaction truth;
- DecisionReadiness hard gates;
- VERIFIED recommendation mapping;
- one Recommendation Authority;
- no fabricated WHY;
- stale callback protection for critical actions;
- E2E observability for blockers.

DEGRADED-кандидаты минимум:

- Plan Lite;
- proactive hints;
- nutrition proactive;
- advanced personalization;
- ResumeSummary if graceful fallback exists;
- advanced WHY;
- recommendation alternatives;
- non-critical profile enrichment.

Но measurement должен подтвердить реальность,
а не механически повторить этот список.

==================================================
4. CAPABILITY MATRIX
==================================================

Обязательно пройти минимум эти capability areas:

1. Identity / Auth
2. MAX conversation entry
3. ConversationState
4. ResumeSummary
5. SemanticUserEvent / input normalization
6. Quick Replies / free text
7. DecisionReadiness
8. Question Resolver / question ledger
9. Safety Architecture v1
10. Goal
11. GoalCandidate
12. Goal lifecycle
13. Goal relevance / continuity
14. Recommendation Resolver
15. CanonicalService
16. TenantOffer mapping
17. Capability mapping
18. Grounded WHY
19. Recommendation lifecycle / recommendation_id
20. PendingBookingIntent
21. Booking
22. Availability
23. Cancellation
24. Reschedule
25. Stale callbacks
26. Provider unavailable handling
27. Offer unavailable handling
28. Memory
29. Memory promotion boundary
30. Analytics / attribution
31. Observability
32. Feature flags / kill switches
33. Food diary
34. Nutrition screening
35. Nutrition proactive
36. Plan Lite
37. Planning Rules plumbing
38. Outbound anti-hallucination guards
39. Cross-surface consistency
40. Pilot catalog subset
41. Test suite / golden E2E
42. Deployment / rollback readiness

Можно добавлять capability,
если она реально блокирует pilot.

==================================================
5. УЖЕ ЗАКРЫТЫЕ OWNER DECISIONS — НЕ ПЕРЕОТКРЫВАТЬ
==================================================

Safety Architecture v1:
FINAL FREEZE.

DecisionReadiness:
canonical states:

    READY
    NEEDS_DISCRIMINATION
    NEEDS_REQUIRED_CONTEXT
    INSUFFICIENT_EVIDENCE
    BLOCKED

DecisionReadiness deterministic.
LLM confidence ≠ readiness.

Stable semantic question_id required.

Asked/resolved ledger required.

Safety CLARIFY имеет приоритет над обычными вопросами.

Resolved question повторяется
только по controlled reason.

ConversationState:
2h inactivity TTL.

ResumeSummary:
24h.
P0 owner = ai-bot-platform.
Storage = Redis.
Не authoritative domain truth.

OD-DR-1:
DRE сначала shadow calibration.
Discrimination thresholds versioned controlled policy.
Hard required-context не требует калибровки.

OD-DR-2:
ASK_BUDGET_EXHAUSTED
→ semantic recommendation blocked.
Разрешён controlled user-directed fallback.
No fabricated recommendation.

OD-DR-3:
PROMOTED_MEMORY сама по себе
не закрывает safety slot.
Safety Rule определяет applicability/validity.

Goal:
Goal = durable desired outcome.
Goal ≠ CurrentNeed.
Goal ≠ Service.
Goal ≠ Plan.
Goal ≠ Booking.

Curated GoalOption click
может сразу создать durable Goal.

Conversational GoalCandidate
не auto-persist.

Multiple active Goals — target semantics.
Current explicit intent > Goal context.

Plan:
Goal не порождает Plan автоматически.

Plan нужен только если coordination adds value:
- multi-step;
- meaningful time-bound coordination;
- dependent actions;
- explicit user request.

PlanEligibility != PlanValidation.

Plan capability-first.

UNKNOWN ≠ invented precision.

Plan does not own provider/slot/price/current availability.

Plan Lite НЕ pilot blocker.

Full:
Plan → PlanRevision → PlanStep → Revalidation
относится к MVP Product Brain v1.

Planning Constraints:
Normative registry owner = ayla-knowledge.

Current measurement:
0 KNOWN authoritative planning rules.

UNKNOWN не заменяется average/false/zero/"обычно".

Package size ≠ required course.

Compatibility:
intentionally unsupported where already decided.

Event context:
SUPPORTED in Product Brain v1.

EVENT_WINDOW:
UNKNOWN until grounded rules exist.

OD-PC-1:
KNOWN planning rule =
allowed evidence class
+ applicability
+ controlled review
+ safety/legal governance when required.

Allowed evidence classes conceptually:
E1 CANONICAL_OWNER_RULE
E2 AUTHORITATIVE_EXTERNAL_PROTOCOL
E3 VERIFIED_DOMAIN_SOURCE
E4 APPROVED_EXPERT_CURATED

LLM / marketing / service description /
user behavior / package size
не являются достаточным evidence.

Recommendation:
one semantic Recommendation Authority across surfaces.

Recommendation ≠ Service ≠ Provider.

WHY requires independent grounded evidence.

No displayable WHY → no WHY block.

Only VERIFIED mapping admitted
to ordinary semantic recommendation path.

Direct user-selected booking
может иметь более широкий catalog scope,
если service identity/safety/transaction truth valid.

Mapping:
UNMAPPED ≠ CANON_GAP.

CanonicalService должен иметь immutable machine identity.

Goal user-owned, not tenant-owned.

PendingBookingIntent:
backend-owned cross-surface handoff.

TTL = 30 min.

Deep link only opaque ref.

Final booking always revalidates transaction truth.

Analytics:
presented ≠ engaged ≠ booked ≠ completed ≠ liked.

Recommendation → Booking attribution
through recommendation_id / BookingIntent.

==================================================
6. ИЗВЕСТНЫЕ MEASUREMENTS / INPUTS
==================================================

Обязательно использовать как evidence
актуальные measurement artifacts:

- docs/MEASUREMENT_DECISION_READINESS_CURRENT.md
- docs/MEASUREMENT_PLANNING_CONSTRAINTS_CURRENT.md
- Goal Candidate measurement
- Goal / Wellness / Plan Authority measurement
- G6 CanonicalService ↔ TenantOffer mapping measurement
- Safety Architecture v1 FINAL FREEZE
- Recommendation resolver contracts/audits
- Conversation State v1.1 reconciled
- current pilot measurement registry
- relevant booking / schedule measurements
- current Linear release/milestone state where available

Не считать старые числа permanent truth.

Если measurement старше допустимого freshness window
или branch/SHA расходится с current canonical branch:

    STALE
    → remeasure targeted boundary only.

Не повторять уже свежий measurement без причины.

==================================================
7. PILOT CATALOG SUBSET
==================================================

Нужно доказательно определить:

- какой salon/tenant является pilot scope;
- сколько реально sellable offers;
- сколько можно включить в AI recommendation subset;
- сколько имеют VERIFIED mapping;
- сколько имеют trustworthy safety semantics;
- сколько можно direct-book;
- сколько имеют availability;
- сколько реально нужны в P1/P2/P3/P4 scenarios.

Не ставь цель "mapping 100% catalog".

Target:

    PILOT_RECOMMENDATION_SUBSET

Только subset, который Ayla действительно понимает.

Для каждой пилотной offer candidate:

    offer_id
    canonical_service_id
    mapping_status
    mapping provenance
    capability relation
    safety state semantics
    active?
    bookable?
    provider coverage?
    availability truth source?

Если текущий VERIFIED count = 0:
зафиксировать pilot blocker,
не придумывать mapping.

==================================================
8. RECOMMENDATION AUTHORITY
==================================================

Проверить все поверхности:

- MAX global
- MAX per-tenant
- Mini App / Home
- backend recommendation endpoint
- legacy chat path

Ответить:

Есть ли сегодня больше одного authority,
способного решить "что лучше пользователю"?

Для каждого:

INPUT
ALGORITHM
CANONICAL MAPPING?
SAFETY?
WHY EVIDENCE?
OUTPUT
CALLERS
ACTIVE?
FEATURE FLAG?

Если существует parallel authority:
классифицировать как pilot blocker,
если она может реально показать
противоречащую Recommendation пользователю.

==================================================
9. SAFETY PILOT GATE
==================================================

Safety implementation проверять end-to-end.

Не достаточно:

    "есть safety code"

Нужно доказать:

UserEvent
 ↓
Evidence
 ↓
Safety producer
 ↓
SafetyResult
 ↓
capability decisions
 ↓
Recommendation / Booking enforcement
 ↓
Outbound response

Обязательно проверить:

- NORMAL
- CLARIFY
- CAUTION
- STOP
- UNKNOWN
- ERROR
- POLICY_CONFLICT
- NOT_APPLICABLE semantics

Критические paths:

- recommendation
- direct booking
- catalog chat booking
- Mini App booking
- master/provider booking if user-facing relevant

requires_health_check
+
no valid SafetyResult
→ fail closed.

Если один surface bypass:
STOP PILOT.

==================================================
10. DECISIONREADINESS PILOT GATE
==================================================

Используй свежий G2-DR measurement.

Проверить implementation status после measurement:

- runtime states exist?
- central authority?
- question_id?
- ledger?
- duplicate detection?
- safety priority?
- delegation?
- free text parity?
- TTL?
- shadow calibration?
- ask budget?

Не принимать отсутствие discrimination calibration
за blocker shadow phase,
если hard gates deterministic.

Но если global ask-vs-act всё ещё LLM-only:
классифицировать как STOP PILOT
для recommendation path.

==================================================
11. GOAL PILOT GATE
==================================================

Проверить минимум:

- create from curated chip;
- conversational GoalCandidate;
- controlled promotion;
- lifecycle ACTIVE / PAUSED / ACHIEVED / ARCHIVED;
- no auto-achieve;
- current explicit intent > Goal;
- continuity next conversation;
- no tenant ownership confusion;
- no Goal copied as transaction truth.

Для pilot допустим ограниченный Goal implementation,
но lifecycle должен быть честным.

Multiple active Goals:
если target semantics ещё не implemented,
классифицировать migration status,
но не автоматически STOP,
если pilot scope сознательно поддерживает один Goal
и не нарушает UX обещание.

==================================================
12. PLAN LITE
==================================================

Plan Lite = optional / DEGRADED.

Проверить:

- capability-level Plan possible?
- no invented timing?
- no invented frequency?
- no invented sequence?
- event context accepted?
- UNKNOWN rendered honestly?
- no fake progress %?
- feature flag?
- kill switch?

Если Plan Lite не готов:
pilot разрешён без него.

==================================================
13. BOOKING PILOT GATE
==================================================

Проверить end-to-end:

Recommendation/direct selection
→ PendingBookingIntent
→ Mini App
→ offer/provider/date/time
→ fresh availability
→ booking creation
→ idempotency
→ My Bookings
→ cancel
→ reschedule

Обязательно проверить:

- stale slot;
- provider unavailable;
- offer inactive;
- price changed;
- duration changed;
- tenant scope changed;
- duplicate submit;
- expired PendingBookingIntent;
- stale callback;
- user returns after handoff;
- direct booking without recommendation.

No silent substitution.

Booking truth только backend.

==================================================
14. MEMORY / CONTINUITY
==================================================

Pilot минимум:

- current explicit user facts safe;
- Goal continuity;
- no assistant→USER evidence contamination;
- no one-off rejection→durable dislike;
- no symptom auto-promotion;
- provenance preserved;
- ResumeSummary separate from UserMemory.

Если advanced memory promotion не готова:
feature can degrade,
но contamination/privacy defect = STOP.

==================================================
15. ANALYTICS / OBSERVABILITY
==================================================

До pilot необязательно иметь красивый dashboard.

Обязательно иметь события/логи,
позволяющие установить:

- conversation_started
- question_presented
- question_resolved
- question_repeated
- safety_result
- safety_error
- recommendation_presented
- recommendation_engaged
- no_verified_candidate
- booking_intent_created
- booking_created
- booking_cancelled
- booking_completed
- stale_callback
- outbound_revise / blocked claim
- internal error/correlation_id

Проверить Recommendation → Booking traceability.

Проверить, можно ли расследовать один
конкретный пользовательский путь
без чтения сырых production-логов вручную.

==================================================
16. PILOT METRICS
==================================================

Минимальные derived metrics:

Recommendation Coverage

    grounded recommendations
    /
    eligible recommendation requests

Recommendation → Booking

    recommendation-attributed bookings
    /
    recommendations presented

Question Efficiency

    questions before useful resolution

Repeated Question Rate

    same semantic question repeated
    without controlled reason

No Verified Candidate Rate

Safety Fail-Closed Rate

Booking Failure Rate

Stale Interaction Rate

Outbound Unsupported Claim Rate

Не оптимизировать metrics за счёт ослабления guards.

==================================================
17. FEATURE FLAGS / KILL SWITCHES
==================================================

Проверить, есть ли controlled disable path минимум для:

- RECOMMENDATIONS
- PLAN_LITE
- NUTRITION_PROACTIVE
- PROACTIVE_HINTS
- optional AI surfaces

Критическое правило:

feature flag не может превращать:

    safety disabled
→ unsafe legacy fallback.

Если mandatory guard unavailable:

    capability unavailable / fail closed.

Проверить fallback:

RECOMMENDATION off
→ direct browse / direct booking remains.

==================================================
18. GOLDEN E2E SUITE
==================================================

Проверить наличие или готовность минимум таких journeys:

GOLD-01 "Хочу массаж"
GOLD-02 "Хочу расслабиться"
GOLD-03 "Не знаю, выбери сама"
GOLD-04 "Хочу другого мастера"
GOLD-05 provider unavailable
GOLD-06 stale slot
GOLD-07 requires_health_check + no SafetyResult
GOLD-08 Safety CLARIFY
GOLD-09 Safety STOP
GOLD-10 no VERIFIED candidate
GOLD-11 Goal creation
GOLD-12 Goal continuity
GOLD-13 resolved question not repeated
GOLD-14 free text vs tap
GOLD-15 stale callback
GOLD-16 Recommendation → Mini App
GOLD-17 Booking completed
GOLD-18 Booking cancelled
GOLD-19 assistant output cannot become USER evidence
GOLD-20 invented planning interval blocked/revised
GOLD-21 direct service booking without Recommendation
GOLD-22 expired PendingBookingIntent
GOLD-23 price/availability changed before final booking
GOLD-24 recommendation WHY without evidence is suppressed

Для каждого:

EXISTS?
LEVEL:
UNIT / CONTRACT / CROSS-BOUNDARY / E2E / LIVE
GREEN?
CURRENT SHA?
BLOCKER?

Не считать старый golden replay
доказательством current E2E,
если он идёт через deprecated pipeline.

==================================================
19. ROLLOUT PLAN
==================================================

Проверить, что технически возможен staged rollout:

Stage 0:
internal team

Stage 1:
5–10 users

Stage 2:
20–30

Stage 3:
50–100

Для каждой стадии определить release gate:

- Safety incidents
- booking errors
- unsupported claims
- repeated questions
- no-verified rate
- drop-off
- recommendation coverage

Не придумывать числовые thresholds,
если owner/policy ещё не заданы.

Пометить:
OWNER_THRESHOLD_REQUIRED.

==================================================
20. DATA / PRIVACY / LEGAL
==================================================

Проверить только pilot-critical seams:

- consent;
- health data handling;
- deletion;
- memory provenance;
- logs containing sensitive data;
- raw transcript retention;
- recommendation evidence privacy;
- food/nutrition personal data;
- cross-surface user identity.

Не проводить новый legal design.

Если canonical legal/privacy gate открыт:
пометить blocker и конкретный dependency.

==================================================
21. DEPLOYMENT / OPERATIONS
==================================================

Проверить:

- current deploy branch;
- staging parity;
- rollback;
- migrations;
- feature flags;
- health checks;
- alerting;
- logs/correlation IDs;
- release concurrency;
- ability to disable AI recommendation quickly;
- backup / DB safety where relevant.

Не делать deploy.

==================================================
22. READINESS CLASSIFICATION
==================================================

Для каждой capability использовать:

CURRENT STATE:

    READY
    PARTIAL
    MISSING
    CONTRADICTS_CANON
    STALE_MEASUREMENT
    UNKNOWN_NOT_MEASURED

PILOT IMPACT:

    STOP
    DEGRADED
    POST_PILOT

OWNER STATUS:

    CLOSED
    OPEN
    NOT_OWNER_DECISION

SPEC STATUS:

    FINAL_FREEZE
    NORMATIVE
    DRAFT_IMPLEMENTATION_READY
    STALE
    MISSING

IMPLEMENTATION:

    DONE
    PARTIAL
    NOT_STARTED
    DEFECT
    UNKNOWN

TEST STATUS:

    E2E_GREEN
    CONTRACT_ONLY
    UNIT_ONLY
    MISSING
    STALE

OBSERVABILITY:

    READY
    PARTIAL
    MISSING

FLAG/FALLBACK:

    AVAILABLE
    MISSING
    NOT_APPLICABLE

==================================================
23. MAIN ARTIFACT
==================================================

Создай:

docs/CONTROLLED_PILOT_READINESS_MATRIX.md

Обязательная структура:

1. Measurement bases
2. Pilot definition
3. Executive verdict
4. Current pilot scope
5. Pilot scenario readiness P1–P10
6. STOP blockers
7. DEGRADED capabilities
8. POST-PILOT scope
9. Capability readiness matrix
10. Safety gate
11. DecisionReadiness gate
12. Goal gate
13. Recommendation gate
14. Mapping gate
15. Booking gate
16. Memory / continuity
17. Nutrition
18. Plan Lite
19. Analytics / observability
20. Feature flags / kill switches
21. Golden E2E coverage
22. Deployment / rollback
23. Data/privacy/legal dependencies
24. Pilot catalog subset
25. Pilot metrics
26. Rollout readiness
27. Open owner decisions
28. Non-owner implementation defects
29. Unknown/not measured
30. Exact reproduction commands
31. Critical path to first 5–10 users

==================================================
24. EXECUTIVE VERDICT FORMAT
==================================================

В начале документа ответь:

CONTROLLED PILOT STATUS:

    GO
    CONDITIONAL_GO
    NO_GO

FIRST 5–10 USERS READY TODAY:
    YES / NO

STOP BLOCKERS COUNT:
    N

DEGRADED CAPABILITIES COUNT:
    N

OPEN OWNER DECISIONS COUNT:
    N

UNKNOWN_NOT_MEASURED COUNT:
    N

И ответь коротко:

1. Что именно сегодня мешает запустить 5–10 пользователей?
2. Какие blockers Safety?
3. Какие blockers Recommendation?
4. Какие blockers Booking?
5. Какие blockers DecisionReadiness?
6. Есть ли достаточный pilot VERIFIED catalog subset?
7. Можно ли отключить Plan Lite без ущерба pilot?
8. Можно ли быстро отключить Recommendation capability?
9. Есть ли E2E доказательство closed loop?
10. Есть ли observability для расследования инцидента?

==================================================
25. CRITICAL PATH
==================================================

В конце дай:

TOP CRITICAL PATH TO PILOT

Формат:

CP-1
Capability:
Current gap:
Why STOP:
Exact owner:
Dependency:
Existing spec:
Implementation work:
Required test:
Required observability:
Can parallelize with:
Exit criterion:

CP-2
...

Не делать огромный backlog.

Нужен только реальный critical path
до первых 5–10 пользователей.

Если 25 проблем,
но только 6 реально блокируют pilot,
в critical path должны быть эти 6.

==================================================
26. OWNER DECISIONS
==================================================

Принести только вопросы,
без которых нельзя определить pilot behavior.

Не спрашивать то, что уже закрыто.

Формат:

OD-PILOT-X

Question:
Current evidence:
Existing canon:
Why canon does not answer:
Option A:
Option B:
Pilot impact:
Blocks:

Если вопрос не меняет pilot behavior:
это не owner decision.

==================================================
27. FRESHNESS RULE
==================================================

Measurement current date = 2026-09-09.

Для fast-changing runtime facts:
используй свежие branches/SHAs.

Если предыдущий measurement снят
на ветке, отстающей от canonical branch,
делай targeted delta recheck,
а не полный повтор.

Каждый runtime claim должен иметь:

repo
branch/ref
SHA
file/function
measurement date

"не замерено" ≠ "отсутствует".

"не найдено" ≠ "false".

UNKNOWN сохраняется.

==================================================
28. STOP CONDITION
==================================================

После readiness matrix STOP.

НЕ исправлять blockers.
НЕ создавать implementation PR.
НЕ менять product scope.
НЕ строить full Plan Engine.
НЕ создавать новую архитектуру.
НЕ назначать числовые rollout thresholds без owner decision.
НЕ обновлять Linear.

Верни владельцу только:

1. artifact path;
2. exact refs/SHAs;
3. CONTROLLED PILOT STATUS;
4. STOP blocker count;
5. top critical path;
6. DEGRADED-but-allowed list;
7. только реальные owner decisions;
8. UNKNOWN/not measured;
9. recommendation:
   "можно ли после закрытия critical path запускать 5–10 пользователей?"