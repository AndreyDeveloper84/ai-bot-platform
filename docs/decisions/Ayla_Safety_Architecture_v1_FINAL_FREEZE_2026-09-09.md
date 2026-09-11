# Ayla Safety Architecture v1

## FINAL FREEZE --- Implementation-ready handoff для агента-оркестратора

**Дата:** 2026-09-09\
**Статус:** FINAL FREEZE / Owner-approved\
**Назначение:** нормативный handoff для реализации Safety Architecture
v1.\
**Режим после получения:** reconcile → plan → delegate → implement →
verify. Не переоткрывать принятые V1--V8 и M1--M14 из-за расхождения с
текущим кодом; эскалировать только реальное противоречие с более сильным
каноном или новый owner blocker.

------------------------------------------------------------------------

# 1. Цель и обязательные инварианты

Оркестратор должен реализовать единую safety-архитектуру Ayla, в которой
LLM понимает язык, извлекает кандидаты сигналов и формулирует
разрешённый текст, но **не является authority** для SafetyState,
медицинской тяжести, противопоказаний, diagnosis/prescription,
timing/compatibility, admissibility или provenance.

Canonical chain:

``` text
UserEvent
→ Evidence Extraction
→ Evidence Origin Validation
→ SafetySignals
→ versioned SafetyRules
→ individual RuleResults
→ aggregate SafetyResult
→ DecisionReadiness / Consumers
→ Response Generation
→ Outbound Validation
→ User
```

Runtime decision должен быть трассируем:

``` text
evidence_ref
→ signal
→ rule_id/rule_version
→ RuleResult
→ policy_version
→ SafetyResult
→ capability decision
→ next action/outbound verdict
```

Отсутствие данных, неизвестное правило, conflict, invalid contract или
runtime error никогда не превращаются в молчаливый `NORMAL`.

------------------------------------------------------------------------

# 2. Канонические оси

## 2.1 SafetyState

Единственный enum:

``` text
NORMAL
CLARIFY
CAUTION
STOP
```

Приоритет applicable rules:

``` text
STOP > CLARIFY > CAUTION > NORMAL
```

Семантика: - **NORMAL** --- safety применима и реально evaluated;
релевантных ограничений нет. - **CLARIFY** --- отсутствует конкретный
required decision-changing fact. - **CAUTION** --- ограниченное
продолжение в пределах capability restrictions; не автоматическое
разрешение/запрет booking. - **STOP** --- applicable rule запрещает
конкретную safety-sensitive capability/action; это не глобальный ban
пользователя.

## 2.2 Отдельные оси

Не добавлять в SafetyState: - applicability:
`APPLICABLE | NOT_APPLICABLE`; - evaluation:
`NOT_REQUIRED | EVALUATED | INCOMPLETE | CONFLICTED | POLICY_CONFLICT | ERROR`; -
capability decision: `ALLOWED | REQUIRES_RESOLUTION | BLOCKED`; -
escalation --- отдельный controlled result; - outbound:
`PASS | REVISE | BLOCK`.

`UNKNOWN` --- отсутствие достаточного знания/evidence, не `NORMAL` и не
противопоказание.

------------------------------------------------------------------------

# 3. Owner Decisions V1--V8 --- CLOSED

## V1 --- CAUTION

`CAUTION` = limited continuation. Conversation продолжается. Booking
разрешён только если конкретный SafetyRule и остальные gates это
допускают. Никакого `CAUTION => booking_allowed=true`.

## V2 --- медицина и право

Принят вариант **C: CAUTION + substantive guidance**.

Медицина: разрешены несколько grounded возможных объяснений, связи и
safe next steps; запрещены diagnosis-as-fact, выдача гипотезы за факт и
prohibited personalized prescription/treatment.

Право: разрешены общий применимый framework, возможные действия, процесс
и факторы исхода; запрещено выдавать неопределённый исход за
гарантированный.

Сам medical/legal intent не означает STOP; независимые red flags
оцениваются отдельно.

## V3 --- red flags

Не использовать `симптом → state`. Controlled policy учитывает signal +
qualifiers/severity + temporal context + associated red flags + required
evidence.

-   достаточный emergency pattern → STOP;
-   потенциальный red flag + missing decision-changing evidence →
    CLARIFY;
-   подтверждённый non-emergency service-relevant factor → CAUTION;
-   после достаточного evidence и отсутствия ограничений → NORMAL.

LLM извлекает факты, но не назначает severity/state.

## V4 --- persistence/resolution

SafetyState не сбрасывается следующей репликой и не лечится общим TTL.

Изменение только через controlled reevaluation на основании нового
допустимого evidence, ответа на required question, authorised resolution
source или rule-specific reassessment policy.

Expiry/validity window → reassessment, **не automatic NORMAL**.
Универсального TTL STOP/CAUTION нет. ConversationState TTL ≠ safety
evidence validity.

## V5 --- response contract

Не фиксировать одну фразу на все случаи.

CLARIFY: один decision-changing controlled question; optional short
reason; запрещены diagnosis/unsupported inference/recommendation до
required evidence.

CAUTION: grounded facts + uncertainty boundary при интерпретации;
allowed bounded hypotheses/general guidance/safe next step; forbidden
diagnosis-as-fact, guaranteed legal outcome, unsupported causality,
prohibited prescription.

LLM формулирует только внутри controlled response contract. Critical
STOP использует controlled response semantics/templates.

## V6 --- REVISE

`REVISE` --- outbound verdict, не CAUTION и не SafetyState.

``` text
Decision Safety: NORMAL / CLARIFY / CAUTION / STOP
Outbound:       PASS / REVISE / BLOCK
```

После REVISE ответ регенерируется в пределах исходного SafetyResult и
проверяется повторно. Outbound validator не ослабляет SafetyState.

## V7 --- HANDOFF/escalation

HANDOFF не SafetyState. SafetyState и escalation независимы. STOP не
означает автоматически HUMAN_HANDOFF; HUMAN_HANDOFF не означает STOP.
Handoff сам не снимает SafetyState. Тип escalation соответствует
причине; обычный salon operator не становится medical resolution
authority.

## V8 --- policy ownership

Canonical Safety Policy находится под version control.

**Нормативный policy source: `ayla-knowledge`.**

Из него строится валидируемый versioned machine-readable artifact.
Runtime не читает Markdown как policy и не создаёт правила
самостоятельно.

Ownership: - `ayla-knowledge`: policy/rules/provenance; -
`ayla-ai-core`: semantic contracts + Safety Engine semantics; -
`ai-bot-platform`: orchestration/session/channel + outbound
validation; - backend Ayla: authoritative domain facts,
catalog/offer/booking truth, `requires_health_check`.

Prompts/comments/local tables не являются safety authority.

------------------------------------------------------------------------

# 4. Safety Matrix Decisions M1--M14 --- CLOSED

## M1 --- STOP scope

STOP блокирует affected capabilities/actions, не пользователя/account.
Acute red flag может блокировать wellness recommendation/booking,
оставляя разрешённую safety/emergency guidance.

## M2 --- STOP vs CLARIFY

Если evidence уже удовлетворяет controlled STOP-condition --- не
задерживать защитное действие дополнительными вопросами. CLARIFY только
для missing fact, который способен изменить state/admissibility.
Неупомянутый признак = UNKNOWN, не ABSENT. Unresolved required evidence
→ fail-closed для affected safety-sensitive action.

## M3 --- Evidence → Signal → Rule → Result

Запрещена плоская `симптом → состояние` модель.

``` text
Raw Evidence → SafetySignal → SafetyRule → RuleResult → SafetyResult
```

Signal сам не определяет state. Presence минимум
`PRESENT | ABSENT | UNKNOWN`; ABSENT требует допустимого evidence.
Regex/NLU/model могут обнаружить signal; policy определяет result.

## M4 --- signal classes

Минимум: 1. Universal Safety Signals. 2. Service-Specific Health
Constraints. 3. Intent/Capability Safety Signals.

Temporal/severity/onset --- qualifiers evidence. `requires_health_check`
--- domain trigger evaluation, не state/contraindication. Universal
safety нельзя ослабить локальным rule.

## M5 --- requires_health_check

`requires_health_check=true` означает mandatory safety evaluation перед
соответствующей recommendation/execution. Не означает danger,
contraindication, CAUTION, STOP или ranking penalty.

``` text
requires_health_check=true + no valid SafetyResult
→ UNKNOWN / fail-closed
```

Target architecture поддерживает controlled `safety_requirements`/policy
refs. Safety semantics привязаны к Capability/CanonicalService + policy,
не к свободному TenantOffer name. Safety выполняется до final
safety-sensitive RecommendationDecision.

## M6 --- CAUTION restrictions

Каждый applicable rule возвращает explicit capability restrictions.
Базовые outcomes:

``` text
ALLOWED
REQUIRES_RESOLUTION
BLOCKED
```

CAUTION не разрешает заполнять missing medical/compatibility/timing
knowledge предположениями. Разрешённый CAUTION не ranking penalty.
Safety ограничивает/исключает; коммерческую альтернативу выбирает
Recommendation Resolver.

## M7 --- diagnoses/medications/professional guidance

User-reported diagnoses, medication use и слова врача --- evidence с
provenance, но не автоматически confirmed medical truth Ayla.
`принимаю X` (health evidence) ≠ `что мне принимать?` (intent safety
signal). `врач разрешил` не bypass Safety Matrix. User-specific
professional instruction не становится global canonical rule. Transient
health evidence не auto-promote в durable memory.

## M8 --- sensitive conditions

Pregnancy и другие sensitive physiological/health facts --- evidence, не
state и не `risk_user`. Влияют только через specific capability +
applicable rule. Relevant fact + missing validated knowledge →
UNKNOWN/unresolved, не invented CAUTION/NORMAL/contraindication.

Отличать `BLOCKED_BY_RULE` от `SAFETY_EVIDENCE_INSUFFICIENT`.

Sensitive health evidence не используется автоматически для commercial
ranking/targeting и не передаётся provider/salon без отдельного
controlled disclosure purpose/consent.

## M9 --- procedures/operations/injuries/timing

Факт события ≠ срок ожидания.

`MIN_INTERVAL`, `MAX_INTERVAL`, `RECOVERY_WINDOW`, `SEQUENCE`,
`COMPATIBILITY`, `INCOMPATIBILITY` требуют versioned controlled rule +
provenance.

Safety отвечает «допустимо ли сейчас?», Planning Constraints --- «какой
grounded timing/sequence constraint?». Оба используют единый source в
`ayla-knowledge`.

`недавно` не превращается LLM в дату. Expired interval → reevaluation,
не NORMAL. Нет rule/provenance → UNKNOWN/INCOMPLETE, не invented timing.

## M10 --- multiple rules

Хранить все individual RuleResults и отдельно aggregate.

``` text
State:       STOP > CLARIFY > CAUTION > NORMAL
Restriction: BLOCKED > REQUIRES_RESOLUTION > ALLOWED
```

Resolution одного rule → reevaluate all. Не задавать вопрос слабого
rule, если он не изменит текущий next action из-за более сильного active
state. Independent signals не создают compound medical inference без
explicit compound rule.

Contradictory authoritative rules → runtime fail-closed + first-class
`POLICY_CONFLICT` + observable incident.

## M11 --- Safety Question Orchestration

LLM не выбирает required safety questions.

``` text
UserEvent
→ Extraction
→ Origin Validation
→ Safety Evaluation
→ Aggregation
→ DecisionReadiness
→ Question Resolver
```

Каждый canonical safety question имеет stable `question_id`.
ConversationState хранит asked/resolved/unresolved отдельно. Asked ≠
resolved.

Reask только с controlled reason (contradictory evidence, correction,
ambiguous answer, material context change, stale evidence и т.п.).
`MODEL_FORGOT` и `LLM_WANTS_MORE_CONFIDENCE` запрещены.

По умолчанию один highest-value decision-changing question/turn;
controlled cluster может собирать тесно связанные fields. Free text
может закрыть несколько requirements. Assistant/model output никогда не
USER evidence. Stale answers revalidate against state revision. Если
reask бесполезен --- controlled fail-closed/alternative outcome, не
loop.

## M12 --- Safety Evidence Model

Evidence --- typed object с provenance, temporal relevance, scope,
source ref.

Концептуально:

``` text
SafetyEvidence
├── evidence_id
├── subject
├── value
├── origin
├── observed_at
├── recorded_at
├── scope
├── source_ref
├── supersedes
└── sensitivity
```

Origins --- closed controlled set. Assistant output/model
reasoning/model hypothesis/previous recommendation не USER evidence.

Authority contextual: authority + scope + temporal relevance +
provenance + rule requirements.

Для mutable CURRENT_STATE новое explicit user evidence supersedes старое
user-reported/memory evidence как current description, не уничтожая
историю. User statement не переписывает authoritative domain event
автоматически. Существенный conflict → `EVIDENCE_CONFLICT`.

UNKNOWN ≠ CONFLICTED.

Scopes минимум концептуально: `CURRENT_STATE`, `EVENT_SPECIFIC`,
`PERSISTENT_REPORTED_FACT`, `TRANSACTION_SPECIFIC`.

SafetyResult не создаёт медицинское evidence обратно.

## M13 --- Runtime SafetyResult

`SafetyResult` --- единственный semantic output Safety Engine.

``` yaml
SafetyResult:
  safety_contract_version: "1.0"
  evaluation_id: "..."
  based_on_state_revision: 42

  applicability: APPLICABLE | NOT_APPLICABLE

  evaluation_status:
    NOT_REQUIRED | EVALUATED | INCOMPLETE |
    CONFLICTED | POLICY_CONFLICT | ERROR

  aggregate_state:
    NORMAL | CLARIFY | CAUTION | STOP | null

  rule_results: [...]
  capability_decisions: {...}
  unresolved_requirements: [...]
  escalation: {...}
  response_constraints: [...]
  evidence_refs: [...]

  policy_version: "..."
  evaluated_at: "..."
```

Consumers не выводят admissibility из CAUTION/STOP самостоятельно;
читают explicit capability decisions
`ALLOWED | REQUIRES_RESOLUTION | BLOCKED`.

Individual RuleResults сохраняются. Unresolved requirements передаются
DecisionReadiness. Escalation отдельна. Response constraints идут в
generation + outbound validation. Sensitive raw evidence не копируется
без необходимости; использовать refs.

Обязательна version provenance: contract version, policy version, rule
ID/version.

Missing/incomplete/conflicted/invalid SafetyResult никогда не трактуется
как NORMAL.

## M14 --- verification/observability/DoD

Тестировать structured semantic output, не дословный LLM copy.

Каждый rule минимум: positive, negative, missing required evidence,
contradictory evidence, stale evidence, resolution/reassessment.

Cross-system regressions обязательны: evidence origin, question loop,
UNKNOWN, N/A, requires_health_check fail-closed, all four states,
multi-rule aggregation, policy/evidence conflicts, stale question,
correction, expiry/reassessment, outbound REVISE/BLOCK, missing/corrupt
policy, unknown contract version.

Technical runtime failure = `ERROR`, не medical STOP. Safety-sensitive
capability fail-closed.

Observability privacy-safe; no raw health text in ordinary metric
labels. Audit reconstructs structured provenance, не chain-of-thought.

Controlled Pilot safety-sensitive recommendation path требует full PASS
canonical golden suite. Missing medical knowledge остаётся UNKNOWN;
agents не заполняют его common-sense guesses.

------------------------------------------------------------------------

# 5. Repository ownership

## `ayla-knowledge`

Owns normative policy, SafetyRule registry, stable IDs/versions,
applicability, required evidence semantics, response constraints,
escalation/resolution/reassessment policy refs, planning/safety
controlled assertions, provenance, validation schema/build/export.

Required: validated machine-readable artifact + policy version
manifest + static validator.

Must not own runtime conversation state or booking truth.

## `ayla-ai-core`

Owns semantic SafetyEvidence/SafetySignal/RuleResult/SafetyResult
contracts; deterministic evaluation/aggregation; capability restriction
aggregation; conflict semantics; DecisionReadiness safety interface;
stable reason semantics.

Must not persist authoritative domain data, use LLM confidence as safety
authority, create absent rules, or contain MAX-specific semantics.

## `ai-bot-platform`

Owns event normalization, ConversationState runtime, question ledger,
state revisions, channel presentation, response-generation
orchestration, outbound `PASS/REVISE/BLOCK`, delivery and escalation
orchestration.

Must not own medical policy, convert assistant text into USER evidence,
independently decide SafetyState, fail-open missing SafetyResult or
weaken restrictions.

## Backend Ayla

Owns authoritative Capability/CanonicalService/TenantOffer facts,
`requires_health_check`, future safety requirement refs,
active/service/provider/booking/availability/transaction truth and
authoritative domain events.

Must not create competing safety/recommendation policy or infer safety
from TenantOffer name.

------------------------------------------------------------------------

# 6. Recommendation and Planning integration

## Recommendation

Canonical ordering:

``` text
candidate discovery
→ hard domain eligibility
→ safety applicability/evaluation
→ capability decisions
→ eligible candidate set
→ semantic/transaction/contextual/quality ranking
→ controlled tie-break
→ RecommendationDecision
```

Safety restriction hard/non-compensable. Forbidden: high rating
compensates STOP. Forbidden ranking penalties for CAUTION or
requires_health_check.

If missing safety evidence, expose unresolved requirement to
DecisionReadiness; Recommendation Resolver does not conduct the
conversation.

MAX/Mini App may render differently but consume one
safety/recommendation semantics.

## Planning

Safety and Plan must use one controlled timing/compatibility source in
`ayla-knowledge`.

No rule_id + provenance → no material interval/compatibility assertion.

Unknown → UNKNOWN / Plan INCOMPLETE.

------------------------------------------------------------------------

# 7. Migration from current implementation

Current discovered model approximately:

``` text
ALLOW
CLARIFY
BLOCK
HANDOFF
```

Do **not** blind-rename enums.

-   old `ALLOW` → NORMAL only if safety was applicable and actually
    evaluated; otherwise inspect for NOT_APPLICABLE/incomplete.
-   old `CLARIFY` → canonical CLARIFY only with required
    evidence/question semantics.
-   old `BLOCK` → inspect reason; may mean STOP, capability BLOCKED,
    outbound BLOCK or technical fail-closed. Never mechanically map all
    to STOP.
-   old `HANDOFF` → remove from state axis; migrate to escalation.
-   new `CAUTION` → first-class state, not disclaimer emulation.
-   existing `REVISE` → outbound verdict, not CAUTION.
-   regex/health-screening checks → detectors/inventory only; do not
    auto-promote to canonical rules.
-   `requires_health_check` → keep as P0 coarse mandatory evaluation
    trigger; cannot fail open.
-   existing health-screening loop → migrate to stable question ledger
    and typed evidence origins.

------------------------------------------------------------------------

# 8. Forbidden shortcuts

Agents MUST NOT: 1. Add CAUTION without capability restrictions. 2.
Rename BLOCK→STOP blindly. 3. Keep HANDOFF as SafetyState. 4. Treat
REVISE as CAUTION. 5. Infer NOT_APPLICABLE from missing safety payload.
6. Treat missing evidence as ABSENT. 7. Treat ERROR as medical STOP. 8.
Let LLM choose SafetyState/severity. 9. Invent
contraindications/timing/compatibility/recovery windows. 10. Promote
assistant output to USER evidence. 11. Let old memory silently override
current explicit user evidence. 12. Use requires_health_check as
contraindication/ranking factor. 13. Use CAUTION as ranking penalty. 14.
Let Recommendation Resolver recalculate safety. 15. Let Safety Engine
choose commercial alternative service. 16. Leak raw health text into
ordinary analytics/log labels. 17. Auto-share sensitive evidence with
provider/salon. 18. Auto-promote transient
symptoms/medications/diagnoses into durable memory. 19. Silently resolve
policy conflicts. 20. Open safety-sensitive Controlled Pilot before
golden suite PASS. 21. Populate missing medical rules from model/common
sense. 22. Create second safety authority in prompts/backend/platform
conditionals.

------------------------------------------------------------------------

# 9. Required implementation sequence

**Phase 0 --- Reconciliation gate.** Inspect all four repos; locate
enums, detectors, prompts, screening, requires_health_check,
recommendation eligibility, outbound guards, handoff, evidence/memory
handling and Mini App paths. Build authority map. Classify each surface:
canonical owner / detector / duplicate authority / migrate / delete.
Report true canon contradictions before semantic change.

**Phase 1 --- Contracts first.** Version SafetyEvidence, SafetySignal,
RuleResult, SafetyResult, capability decisions,
evaluation/applicability, escalation, response constraints, unresolved
requirements, reason codes. Add impossible-combination invariant tests.

**Phase 2 --- Policy source/build.** In ayla-knowledge create
registry/schema, stable IDs, manifest/version, validator and
build/export artifact. Do not bulk-invent medical content.

**Phase 3 --- Evidence boundary.** Typed origins/events, scopes,
temporal semantics, conflict semantics, refs; prohibit
SafetyResult→medical-evidence feedback. First reproduce and kill
assistant-output health-screening bug.

**Phase 4 --- Safety Engine.** Applicability, four states, individual
rules, aggregation, capability restrictions,
incomplete/conflicted/error, policy conflicts, reassessment/resolution
hooks.

**Phase 5 --- Question orchestration.** DecisionReadiness integration,
question IDs, ledger, reask reasons, state revision, stale handling,
highest-value question, no loops.

**Phase 6 --- Consumers.** Migrate Recommendation Resolver,
booking/execution, Mini App recommendation surfaces, MAX and
requires_health_check. Consumers read capability decisions and do not
reinterpret states.

**Phase 7 --- Outbound.** Response constraints, PASS/REVISE/BLOCK,
regeneration after REVISE, no weakening, controlled critical STOP
responses.

**Phase 8 --- Observability/audit/privacy.** Structured audit,
privacy-safe metrics, conflicts, reasks, outbound and engine errors.

**Phase 9 --- Golden suite / pilot gate.** Full end-to-end PASS before
safety-sensitive Controlled Pilot.

------------------------------------------------------------------------

# 10. Minimum Golden Suite

Must cover at least: 1. NORMAL. 2. CLARIFY→NORMAL. 3. CLARIFY→CAUTION.
4. CLARIFY→STOP. 5. CAUTION + recommendation ALLOWED. 6. CAUTION +
booking ALLOWED. 7. CAUTION + booking REQUIRES_RESOLUTION. 8. CAUTION +
booking BLOCKED. 9. STOP blocks affected capability only. 10. UNKNOWN.
11. NOT_APPLICABLE. 12. requires_health_check + missing result
fail-closed. 13. EVIDENCE_CONFLICT. 14. POLICY_CONFLICT. 15. Multiple
simultaneous rules. 16. Strongest rule resolves; weaker remaining state
becomes aggregate. 17. Missing signal stays UNKNOWN, not ABSENT. 18.
Explicit user correction. 19. Stale evidence→reassessment. 20. Stale
question/callback. 21. Answered question not repeated. 22. Reask without
allowed reason rejected. 23. Free text resolves multiple requirements.
24. Assistant-output-as-user-evidence regression. 25. Model hypothesis
cannot create ConfirmedEvidence. 26. Professional-reported-by-user not
authoritative automatically. 27. Sensitive fact not global user block.
28. Sensitive fact not provider-visible by default. 29. CAUTION not
ranking score. 30. requires_health_check not ranking score. 31. Unknown
timing/compatibility produces no invented interval. 32. Outbound REVISE.
33. Outbound BLOCK. 34. Unknown safety contract version. 35.
Missing/corrupt policy artifact. 36. Safety Engine timeout/error. 37.
Technical ERROR not rendered as detected medical risk. 38.
NOT_APPLICABLE emits no `ELIG_SAFETY_CLEARED`. 39. Real evaluated NORMAL
may emit `ELIG_SAFETY_CLEARED`. 40. Safety-sensitive candidate cancels
N/A.

------------------------------------------------------------------------

# 11. Observability minimum

Privacy-safe semantic metrics/events:

``` text
safety_evaluations_total
safety_state_total{state}
safety_rule_activated_total{rule_id}
safety_incomplete_total{reason}
safety_evidence_conflict_total
safety_policy_conflict_total
safety_question_reask_total{reason}
safety_outbound_revise_total{reason}
safety_outbound_block_total{reason}
safety_engine_error_total{reason}
```

No raw symptoms/diagnoses/medications/user text in ordinary labels.

Audit path:

``` text
UserEvent → evidence refs → signals → rule/version
→ RuleResults → aggregation → SafetyResult
→ DecisionReadiness → question/action → outbound verdict
```

No chain-of-thought storage.

------------------------------------------------------------------------

# 12. Definition of Done

Safety Architecture v1 is DONE only when:

-   [ ] Canonical versioned Safety Policy exists in `ayla-knowledge`.
-   [ ] Machine-readable artifact builds and validates.
-   [ ] Stable rule_id/rule_version/policy_version exist.
-   [ ] Semantic contract versioned.
-   [ ] Evidence origins typed.
-   [ ] Assistant output cannot become USER evidence.
-   [ ] SafetySignals separate from SafetyRules.
-   [ ] NORMAL/CLARIFY/CAUTION/STOP first-class.
-   [ ] UNKNOWN/INCOMPLETE and NOT_APPLICABLE separate axes.
-   [ ] HANDOFF separate escalation.
-   [ ] REVISE/BLOCK outbound-only.
-   [ ] Capability decisions explicit.
-   [ ] Multi-rule/state/restriction aggregation deterministic.
-   [ ] Evidence conflicts fail closed.
-   [ ] Policy conflicts fail closed and observable.
-   [ ] question_id ledger works.
-   [ ] Reask requires controlled reason.
-   [ ] Stale question revalidation works.
-   [ ] requires_health_check cannot fail open.
-   [ ] Safety precedes final safety-sensitive RecommendationDecision.
-   [ ] Recommendation Resolver does not recalculate safety.
-   [ ] CAUTION and requires_health_check are not ranking penalties.
-   [ ] STOP does not globally block user/account.
-   [ ] Escalation does not clear SafetyState.
-   [ ] Expiry triggers reassessment, not NORMAL.
-   [ ] Sensitive evidence does not leak to ordinary analytics/provider
    by default.
-   [ ] Transient health facts not auto-promoted to durable memory.
-   [ ] Unknown timing/compatibility remains UNKNOWN.
-   [ ] Outbound constraints validated after generation.
-   [ ] Unknown contract/policy versions fail closed for affected
    safety-sensitive capability.
-   [ ] Technical runtime error not represented as medical risk.
-   [ ] Static policy validation blocks invalid artifact publication.
-   [ ] Golden suite full PASS.
-   [ ] Negative/adversarial tests PASS.
-   [ ] MAX/Mini App safety semantics consistent.
-   [ ] Controlled Pilot gate enforced.

Release gate:

``` text
Golden Safety Suite != FULL PASS
→ NO safety-sensitive Controlled Pilot
```

Completeness of all medical rules is **not** required for engine
completion. Missing validated knowledge remains UNKNOWN.

------------------------------------------------------------------------

# 13. Required orchestrator deliverables

Before declaring completion return: 1. Reconciliation report: current
implementation vs this frozen contract. 2. Authority map of every
safety-related surface and canonical owner. 3. Repo-by-repo
implementation/dependency plan. 4. Migration map from old
ALLOW/CLARIFY/BLOCK/HANDOFF and outbound mechanisms. 5. Policy
registry/build implementation. 6. Semantic contract implementation. 7.
Evidence-origin + question-loop implementation. 8. Safety Engine
implementation. 9. Consumer migrations. 10. Outbound validator
alignment. 11. Golden/adversarial test report. 12. Observability/privacy
audit. 13. Remaining UNKNOWN policy coverage, explicitly listed and
never guessed. 14. Final DoD gate report with PASS/FAIL evidence for
every item. 15. Exact commits/PRs/repository paths.

------------------------------------------------------------------------

# 14. Directive to the agent-orchestrator

Treat this document as **owner-approved FINAL FREEZE for Safety
Architecture v1**.

Do not reopen V1--V8 or M1--M14 merely because current code differs.

Before implementation, reconcile this contract against current
repository reality. If a genuine contradiction exists with a stronger
canonical source or implementation requires a new owner decision:

1.  stop only the affected semantic branch;
2.  show exact canon/code evidence;
3.  state the contradiction;
4.  propose the smallest required owner decision;
5.  continue independent non-blocked tracks.

Delegate independent tracks where useful, but independently verify every
agent result before integration.

Agents must not invent safety/medical rules, turn UNKNOWN into plausible
defaults, infer authority from existing code, solve missing provenance
with prose, duplicate policy across repositories, or weaken fail-closed
behavior for UX convenience.

**STOP DESIGN. IMPLEMENT THE FROZEN CONTRACT.**
