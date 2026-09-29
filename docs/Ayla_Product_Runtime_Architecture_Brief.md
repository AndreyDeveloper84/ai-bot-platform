# Ayla --- Product & Runtime Architecture Brief

---

# ⚠️ РАЗБОР ГЛАВНОГО ОКНА — 2026-08-18. Читать до запуска окна по этому брифу

Бриф хорош: он ставит верную рамку («LLM понимает язык и выражает решения; система владеет истиной, правами, переходами и побочными эффектами») и правильно запрещает семь антипаттернов. Спорить с направлением не буду.

Но он **описывает систему как ненаписанную, тогда как большая её часть написана.** Окно, запущенное по нему без поправок, начнёт проектировать то, что уже работает на пилоте, и не заметит настоящих разрывов.

Ниже — измеренное состояние, неточности и то, что я предлагаю изменить в самом брифе.

Классы: **VERIFIED** — прочитано в коде или замерено на пилоте.

---

## §A. Что уже построено — бриф этого не учитывает

**VERIFIED** по `ai-bot-platform` и `beautygo_backend@origin/dev`.

| Раздел брифа | Что уже есть |
|---|---|
| §5 «Target runtime architecture» | `apps/orchestrator/pipeline.py` — **1537 строк** работающего конвейера; `tool_invoker.py`, `composer.py`, `concierge.py`, `channel_registry.py`, `discovery.py`, `handoff.py`, `health.py` |
| §7 «Avoid one giant FSM» | 16 отдельных навыков в `apps/skills/`, реестр и диспетчер — разделение уже сделано |
| §10 «Memory architecture» | `apps/orchestrator/memory/`: `coordinator.py`, `personal_context.py`, `short_term.py` |
| §15 «Safety architecture» | `apps/orchestrator/safety/`: `gate.py`, `pre_check.py`, `post_check.py`, `voice_check.py` — **гейт до и после генерации уже разделён** |
| §12 Layer 2 «Procedure → Specialist» | `ai/application/services/recommendation_engine.py` — **508 строк**, взвешенный скоринг |
| §13 «provenance and attribution» | `ScoreBreakdown.top_reasons()` — объяснение «почему этот» уже возвращается |
| §14 «Economic neutrality» | веса: рейтинг 0.30, расстояние 0.25, доступность 0.20, совпадение услуги 0.15, история 0.10. **Коммерческого веса нет ни одного** — нейтральность уже соблюдена, причём с проверкой `assert sum == 1.0` |
| §23 `ayla-ai-core` | существует отдельным приватным репозиторием, подключается через `pyproject.toml`, версия видна в логе пилота при старте |

**Что это значит для брифа.** Разделы 5, 7, 10, 15 надо переписать из «спроектировать» в «сверить существующее с намерением и назвать расхождения». Это другая работа и другой объём.

---

## §B. Неточности — по пунктам

### B-1. §12 Layer 1 подан как равный Layer 2. Он не равный: его нет вообще

Бриф описывает два слоя рекомендаций симметрично. Замер:

- **Layer 2 (Procedure → Specialist)** — построен, 508 строк, с объяснением.
- **Layer 1 (Goal → Next Best Action / Procedure)** — **ноль строк.**

Причём `RecommendationQuery` (`recommendation_engine.py:80`) принимает `client_lat`, `client_lon`, `city`, `category_id`, `price_max`, `min_rating`, `limit` — **поля `goal` в нём нет**. Движок отвечает на вопрос «какой мастер лучше для выбранной услуги», а не «что этому человеку нужно сделать».

**Это и есть главный разрыв всего брифа, и он должен быть назван в Executive summary, а не в §12.** Всё остальное — вокруг него.

### B-2. §8 «Question Registry» описан как существующая концепция

Замер: `question_registry`, `QuestionRegistry`, `slot_filling` — **0 совпадений** в коде. Есть 49 файлов со словом `clarif`, но это разрозненные уточнения внутри навыков, а не реестр.

Раздел надо пометить как **проектируемый с нуля**, иначе окно будет искать то, чего нет, и решит, что плохо ищет.

### B-3. §23 требует «сверить с матрицей ответственности» — сверять сейчас нельзя

`ayla-knowledge/00 Foundation/Ayla Repository Responsibility Matrix.md` **изменён и не закоммичен**, и весь каталог `07 UX/` **не в git вообще** (`git ls-files "07 UX/"` → 0). Канон существует в одном экземпляре на диске, без истории — DRF-1148.

**Пока это не починено, любая «сверка с каноном» опирается на файл, который никто не может воспроизвести.** Поставить как предусловие раздела.

### B-4. §15 Safety: гейты есть, но продуктовое решение противоположно брифу

Бриф разворачивает архитектуру безопасности как обязательную часть MVP. Фактически: `apps/skills/health_screening/` существует, но в спецификации онбординга (`docs/screens/customer-onboarding-flow.md`, шаг S4) он помечен **SKIPPED FOR PILOT** решением владельца (Q-TAU-O2), а покрытие переложено на серверный override анкеты.

Бриф обязан это отражать: либо решение пересматривается, либо §15 на пилоте не активен. Молчание здесь опасно — это раздел про вред здоровью.

### B-5. §3 (C01–C05) конфликтует с уже принятой и построенной моделью входа

Бриф проектирует C01 как «Goal-first Welcome» экран Mini App. Но вход в продукт **уже спроектирован и построен — в переписке с ботом**, не в Mini App:

> `docs/screens/customer-onboarding-flow.md`: «**Onboarding flow lives entirely в bot DM.** Mini App opens via deeplink only после customer's first action choice.»

S1 приветствие → S2 согласие 152-ФЗ → S3 «кто такая Ayla» → **S5 выбор первого действия**, где среди шести кнопок есть «🎯 Выбрать цель». Реализовано в `apps/skills/welcome/skill.py`, 709 строк.

То есть **гибридный вход, который бриф предлагает как гипотезу, уже выбран и написан.** Не хватает ровно двух вещей, обе заведены: экран выбора цели не существует (DRF-1163), а слаги кнопок не совпадают с маршрутами фронта (DRF-1167).

**Это меняет объём C01 радикально**: не «спроектировать вход», а «достроить одну недостающую ветку в существующем».

### B-6. §25 Phase E «Existing fulfillment integration» недооценена в обе стороны

Транзакционный контур построен полнее, чем предполагает фаза: каталог, мастера, слоты под конкретную услугу, создание, перенос, отмена, зеркало броней, напоминания, отзывы, день салона, закрытие визита с атрибуцией.

Но там же есть **незакрытая дыра, которую бриф не видит**: у салонных ручек `/tenants/me/` модель доступа не достроена — они требуют `IsAuthenticated + IsProApp + IsTenantAdmin`, а как сервисный вызов бота получает такую сессию, не описано ни в одном из репозиториев. Фаза E обязана начинаться с этого, иначе упрётся на середине.

### B-7. §2 «Product thesis» — тезис верен, но пилот проверяет не его

Бриф: «primary Ayla experience is goal-first, not catalog-first».

Факт: `/customer/main` рендерит `CustomerRecordsScreen` (список записей), `HelloScreen` описан в коде как «auth round-trip + **CTA into catalog**». Вся работа 14–18 августа шла на **стороне предложения** — салонный бот, кабинет мастера, админка, операционка.

**Расхождение между тезисом и тем, что строится, надо назвать прямо в брифе,** а не оставлять окну как открытие. Иначе первое, что окно сделает, — напишет об этом отчёт, который уже написан.

---

## §C. Рекомендации к самому брифу

**1. Добавить раздел 0 «Измеренное состояние на дату».** С таблицей §A и явной пометкой, что бриф описывает **дельту**, а не систему с нуля. Без этого раздел 26 («что должен сделать агент») толкает окно на проектирование поверх работающего кода.

**2. Перенести B-1 в Executive summary.** Единственное предложение, которое должно там стоять: *слой «цель → следующее действие» отсутствует полностью; слой «процедура → специалист» построен и объясняет свои решения.* Всё остальное — детали вокруг этого факта.

**3. Разделить бриф на два документа.** Сейчас в нём смешаны:

- **архитектурная рамка** (разделы 5–22) — долгоживущая, ревизия существующего;
- **план MVP** (разделы 3, 25) — короткоживущий, зависит от продуктовых решений владельца.

Первый живёт в `ayla-knowledge` как канон. Второй — в `docs/` как бриф окна. Смешение приводит к тому, что план устаревает и тянет за собой рамку.

**4. Убрать из §26–27 требование «проверить всё».** Двадцать девять разделов × «сверь с кодом» — это не задание, а список тем. Окно с таким брифом либо утонет, либо сделает поверхностно. Предлагаю сузить первый проход до трёх вопросов:

- где именно провести границу «цель → услуга» и кто ей владеет (домен, движок, каталог);
- что делать с уже написанным входом в боте — достраивать или заменять;
- какой минимальный контракт `Goal` нужен, чтобы `RecommendationQuery` смог его принять.

**5. Ввести класс доказательности в требования к отчёту.** У нас это уже норма (VERIFIED / INFERRED / UNKNOWN), и она за три дня десять раз спасла от решений, принятых по рассуждению. Бриф этого не требует — стоит потребовать.

**6. Явно назвать, чего окно НЕ трогает.** Работают три окна: салонный бот, салонная админка, канон. Пересечение по файлам приведёт к конфликту веток — 13.08 это уже стоило коммита, уехавшего на чужую ветку.

---

## §D. Чего я не проверял

- Разделы 16–22 (Consistency Guard, confidence, recovery, observability, сценарии, инварианты) — по существу не сверял, только по наличию модулей.
- Содержимое `ayla-ai-core` — репозиторий приватный и локально не открывался.
- Соответствие построенных экранов их UX-контрактам.
- Тезисы §6 и §11 (structured decision context, runtime context builder) — в коде есть `composer.py` и `memory/coordinator.py`, но совпадают ли они по смыслу с брифом, я не устанавливал.

**Эти пункты — честный список того, что окну придётся померить самому.**

---

**Status:** Working architecture brief for agent refinement\
**Scope:** Client goal-first MVP, conversational runtime,
context/memory, recommendation, safety, fulfillment integration,
reliability and evaluation.\
**Purpose:** Capture the current shared understanding in one place so an
implementation/review agent can refine it against the canonical
repositories and existing code.

------------------------------------------------------------------------

## 1. Executive summary

Ayla must not be implemented as either:

1.  a huge hand-written dialogue tree (`if/else` for every user path);
    or
2.  an unconstrained LLM that receives the conversation and
    independently decides what is true, what to remember, what action to
    execute, and what to recommend.

The target architecture is **hybrid**:

> **Deterministic orchestration + structured domain state + LLM for
> language understanding/rendering + authoritative backend services +
> independent safety/policy gates + explicit recovery and evaluation.**

The core rule is:

> **The LLM understands language and expresses decisions. The system
> owns truth, permissions, state transitions and side effects.**

Ayla should appear flexible and conversational to the user while
internally operating on structured state, explicit capabilities and
guarded actions.

------------------------------------------------------------------------

## 2. Product thesis

The primary Ayla experience is goal-first, not catalog-first.

The user should be able to begin with a human intention such as:

-   improve appearance;
-   feel better;
-   relax/recover;
-   prepare for an event;
-   or "I don't know where to start".

Ayla progressively turns that into an actionable next step:

``` text
Human goal
    ↓
Desired outcome
    ↓
Relevant context
    ↓
Safety / eligibility
    ↓
Next Best Action
    ↓
Recommended procedure
    ↓
Specialist
    ↓
Availability
    ↓
Booking
    ↓
Visit
    ↓
Feedback / result
    ↓
Goal-centric continuation
```

The existing catalog remains a **secondary fast path** for a user who
already knows exactly which service is needed.

------------------------------------------------------------------------

## 3. Client MVP screen model

### C01 --- Goal-first Welcome

Purpose: identify the user's broad goal or route them into
conversational discovery.

Primary path:

``` text
“What do you want to achieve/change?”
    ↓
structured broad goal
```

Secondary paths:

-   "I don't know where to start" → conversational goal discovery;
-   "I already know the service" → existing catalog/booking path.

Returning users with an active goal should normally enter a goal-centric
Home/Today surface rather than C01.

### C02 --- Goal Context / Desired Outcome

Purpose: turn a broad goal into a more specific desired result.

Example:

``` text
Goal: improve appearance/body
Desired outcomes:
- reduce swelling
- improve skin appearance
Primary outcome: reduce swelling
```

C02 describes **what the user wants**, not which procedure they need.

### C03 --- Adaptive Context Clarification

Purpose: collect only missing facts that can materially change the
recommendation.

Principles:

-   2--4 meaningful questions before the first recommendation where
    possible;
-   one decision per interaction;
-   questions are adaptive;
-   do not re-ask facts Ayla already knows;
-   free text is a fallback, not the default;
-   safety questions are a distinct interaction type;
-   the user can inspect what Ayla is taking into account;
-   sensitive safety context is not casually exposed as ordinary chips.

C03 is not a fixed questionnaire. It is a **context acquisition
engine**.

Conceptual state:

``` text
Known context
+ required context
→ missing context
→ next best question
```

A reusable component should expose accumulated context, for example:

``` text
✓ Considered 3 facts >
```

Opening it may show:

-   active goal;
-   primary outcome;
-   relevant lifestyle/context facts;
-   time horizon;
-   ability to correct facts.

Before C04, Ayla should provide a compact summary of the context
actually used.

### C04 --- Ayla Recommendation

Purpose: answer:

> "What should I do next, and why is that the best next step for me?"

C04 is the primary proof of Ayla's product value.

It must include:

-   Next Best Action;
-   human-readable explanation;
-   provenance/context used;
-   uncertainty where relevant;
-   `recommendation_id`.

Without "why", Ayla risks becoming a filtered catalog.

### C05 --- Recommended Procedure

Purpose: connect the strategic recommendation to a concrete executable
service/procedure.

Model:

``` text
C04: WHAT to do
C05: HOW / with which procedure
Existing fulfillment: WHO / WHEN / BOOK
```

C05 should normally present:

-   one primary procedure recommendation;
-   price/duration when authoritative;
-   explanation;
-   at most a small number of alternatives;
-   rejection path and rejection reason;
-   no-eligible-recommendation state;
-   CTA into the existing specialist/booking flow.

After C05, the MVP should reuse the existing fulfillment flow wherever
possible rather than rebuilding booking.

------------------------------------------------------------------------

## 4. The central architectural problem: branching

The number of possible conversations grows combinatorially if every path
is modeled as a hand-written tree.

Do **not** encode:

``` text
C01 → C02 → C03.1 → C03.2 → C03.3 → ...
```

as the core business logic.

Instead model:

``` text
What does the user want?
What is already known?
What is confirmed?
What is missing?
Is anything safety-critical?
Which actions are currently allowed?
What is the best next action?
```

The same runtime can therefore support different paths:

``` text
User A:
Goal → Q1 → Safety → Recommendation

User B:
Goal → Q1 → Q2 → Q3 → Safety → Recommendation

User C:
Goal → context already known → confirm one fact → Recommendation
```

------------------------------------------------------------------------

## 5. Target runtime architecture

``` text
USER
  │
  ▼
Conversation Runtime
  │
  ▼
Turn Understanding
  ├── intent / goal / entities
  └── candidate facts / user corrections
  │
  ▼
Runtime Context Builder
  ├── active goal
  ├── relevant memory projection
  ├── recent conversation
  ├── authoritative backend facts
  ├── consent
  ├── safety state
  ├── recommendation state
  └── available capabilities/tools
  │
  ▼
Decision Orchestrator
  ├── answer
  ├── ask clarification
  ├── confirm candidate fact
  ├── call tool
  ├── generate recommendation
  ├── safe fallback
  └── escalate / declare uncertainty
  │
  ▼
Policy / Safety Guard
  │
  ▼
Consistency Guard
  │
  ▼
Response Renderer / LLM
  │
  ▼
USER
```

The orchestration layer, not the LLM, owns "what happens next".

------------------------------------------------------------------------

## 6. Structured decision context

Each turn should operate on a structured snapshot rather than an
unbounded transcript.

Illustrative model:

``` python
DecisionContext(
    user_intent=...,
    active_goal=...,
    primary_outcome=...,
    known_context=...,
    missing_context=...,
    consent_state=...,
    safety_state=...,
    recommendation_state=...,
    interaction_state=...,
    available_capabilities=...,
    recent_turns=...,
)
```

The orchestrator returns a constrained action:

``` python
NextAction(
    type="ASK_QUESTION",
    question_id="time_horizon",
    reason="MISSING_REQUIRED_CONTEXT",
)
```

or:

``` python
NextAction(
    type="CALL_TOOL",
    tool="find_available_specialists",
)
```

or:

``` python
NextAction(
    type="GENERATE_RECOMMENDATION",
)
```

or:

``` python
NextAction(
    type="SAFE_FALLBACK",
    reason="INSUFFICIENT_CONTEXT",
)
```

------------------------------------------------------------------------

## 7. Avoid one giant FSM

Do not create a monolithic enum containing every conversational
micro-state.

Separate state by domain/lifecycle.

Example:

### Conversation state

``` text
active
paused
completed
```

### Interaction state

``` text
goal_discovery
goal_refinement
context_clarification
recommendation_explanation
fulfillment
```

### Recommendation state

``` text
insufficient_context
eligible
generated
accepted
rejected
blocked
```

### Transaction state

Booking/payment/appointment lifecycle remains authoritative in the
transactional backend.

This prevents the AI runtime from becoming the owner of business truth.

------------------------------------------------------------------------

## 8. Question Registry

C03 questions should be data-driven rather than hard-coded into mobile
navigation or prompts.

Illustrative schema:

``` yaml
id: lifestyle_activity
type: single_choice

applies_to:
  - improve_body
  - recovery

required_for:
  - recommendation.body_procedure

options:
  - sedentary
  - active
  - training
  - mixed

skip_policy: allowed
sensitivity: normal
```

Safety example:

``` yaml
id: procedure_safety_gate
type: multi_select

required_for:
  - recommendation.procedure

skip_policy: forbidden
sensitivity: safety_critical

fallback_on_unknown:
  safe_recommendation_only
```

The registry should define semantics and eligibility for asking a
question. UI copy may be rendered separately.

A question should exist only when its answer can alter:

-   eligibility;
-   ranking;
-   recommendation;
-   safety;
-   or a meaningful explanation.

Do not collect data merely because it "might be useful later".

------------------------------------------------------------------------

## 9. LLM responsibility boundary

The LLM is appropriate for:

-   understanding natural language;
-   extracting candidate structured meaning;
-   resolving linguistic variation;
-   proposing interpretations;
-   conversational clarification;
-   rendering explanations;
-   adapting tone and wording;
-   summarizing relevant context.

The LLM must not be the authority for:

-   whether a booking exists;
-   whether payment succeeded;
-   current availability;
-   durable user facts;
-   consent state;
-   eligibility/safety policy;
-   authoritative prices;
-   irreversible actions;
-   final transactional state.

The LLM should choose only from a constrained action vocabulary, e.g.:

``` text
ANSWER_USER
ASK_CLARIFICATION
PROPOSE_GOAL
PROPOSE_FACT
REQUEST_TOOL
EXPLAIN_RECOMMENDATION
DECLARE_UNCERTAINTY
SAFE_FALLBACK
```

Outputs should be schema-validated before execution.

------------------------------------------------------------------------

## 10. Memory architecture

Do not let an LLM directly write arbitrary durable memory.

Use a proposal pipeline:

``` text
User statement
    ↓
Candidate fact extraction
    ↓
Memory Proposal
    ↓
Policy / sensitivity check
    ↓
Consent check where required
    ↓
Validation / conflict handling
    ↓
Durable fact
```

Example:

``` text
User: “I train three times a week.”

MemoryProposal:
  type = training_frequency
  value = 3/week
  source = explicit_user_statement
  confidence = high
```

The runtime can use relevant memory projections, but durable storage
remains owned by the backend.

Facts should carry provenance and, where useful:

-   source;
-   confidence;
-   freshness;
-   sensitivity;
-   last confirmed time;
-   supersession/correction relationship.

Previously known facts should not always be silently trusted forever.
Where freshness matters, Ayla can confirm:

> "I remember that you train three times a week. Is that still current?"

------------------------------------------------------------------------

## 11. Runtime Context Builder

Do not send the complete user history and all product data to the model
on every turn.

Build a bounded context projection containing only what is relevant to
the current decision.

Example:

``` json
{
  "active_goal": "...",
  "primary_outcome": "...",
  "relevant_facts": [],
  "missing_context": [],
  "current_interaction": "...",
  "safety": {},
  "consent": {},
  "recent_turns": [],
  "tool_results": []
}
```

Benefits:

-   lower latency;
-   lower token cost;
-   less irrelevant context;
-   fewer contradictions;
-   easier testing;
-   easier privacy control;
-   deterministic provenance.

------------------------------------------------------------------------

## 12. Recommendation architecture

Ayla effectively needs two recommendation layers.

### Layer 1 --- Goal → Next Best Action / Procedure

New intelligence:

``` text
Goal
+ Desired outcome
+ Relevant context
+ Safety
    ↓
Next Best Action
    ↓
Procedure recommendation
```

### Layer 2 --- Procedure → Specialist

Existing/previously built ranking can be reused:

``` text
Procedure
+ location
+ availability
+ rating
+ history
    ↓
Specialist ranking
```

The two layers must not be conflated.

The first answers:

> "What should I do?"

The second answers:

> "Who is the best available person to do it?"

------------------------------------------------------------------------

## 13. Recommendation provenance and attribution

Every recommendation that can lead to an action should have a stable
identifier.

Conceptual chain:

``` text
Goal
  ↓
Recommendation R-123
  ↓
Procedure S-45
  ↓
Specialist M-17
  ↓
Appointment A-829
  ↓
Visit
  ↓
Feedback
```

Preserve `recommendation_id` through fulfillment so the system can
measure:

-   recommendation shown;
-   explanation opened;
-   accepted/rejected;
-   rejection reason;
-   booking started;
-   booking confirmed;
-   visit completed;
-   feedback/result.

Without this chain, Ayla cannot learn whether its recommendations were
useful.

------------------------------------------------------------------------

## 14. Economic neutrality

Recommendation ranking must serve the user rather than silently optimize
salon economics.

If:

``` text
Procedure A = better user fit
Procedure B = higher commercial value
```

Ayla must not silently promote B because it is more profitable.

Commercial incentives, sponsorship or ranking modifiers must never
masquerade as personal recommendation logic.

------------------------------------------------------------------------

## 15. Safety architecture

Safety must be a system capability, not merely prompt wording.

Pattern:

``` text
User input
   ↓
Safety signal detection
   ↓
Safety / eligibility policy
   ↓
Allowed / restricted / blocked / uncertain
   ↓
Orchestrator
```

If safety is uncertain, the system should become **more conservative**,
not more confident.

Important invariant:

> **Less reliable context → less aggressive recommendation.**

Safety may:

-   remove procedures from eligibility;
-   require an additional clarification;
-   force a safer recommendation class;
-   prevent procedure recommendation;
-   trigger an appropriate fallback.

Safety-critical questions may have different skip rules from ordinary
personalization questions.

------------------------------------------------------------------------

## 16. Consistency / Coherence Guard

Before a user-visible response or side effect, verify that it does not
contradict authoritative state.

Examples:

-   backend says appointment is cancelled → Ayla cannot claim it is
    confirmed;
-   availability tool failed → Ayla cannot invent a slot;
-   user corrected a fact → old fact cannot remain active without
    explicit conflict handling;
-   safety policy blocks a procedure → recommendation cannot contain it;
-   context is insufficient → response cannot pretend to be certain.

This guard is a key defense against "smart-sounding but wrong" behavior.

------------------------------------------------------------------------

## 17. Confidence and uncertainty

Confidence should exist internally for structured interpretations, not
as decorative percentages shown to users.

Useful confidence dimensions:

-   intent confidence;
-   goal inference confidence;
-   extracted fact confidence;
-   recommendation confidence;
-   safety certainty.

Example:

``` text
goal confidence = high
→ proceed

goal confidence = low
→ confirm:
“Am I right that your main goal right now is recovery?”
```

Ayla should explicitly communicate uncertainty when it matters.

------------------------------------------------------------------------

## 18. Failure and fallback model

Every critical dependency needs a defined failure mode.

### Intent/model failure

Fallback:

-   simple clarification;
-   do not fabricate intent.

### Memory unavailable

Fallback:

-   continue using current-session context;
-   do not pretend remembered information exists.

### Recommendation service unavailable

Fallback:

-   do not invent a recommendation;
-   explain that a reliable recommendation cannot currently be produced;
-   offer an allowed alternative path.

### Availability unavailable

Fallback:

-   do not display stale/unverified availability as live;
-   allow retry or a non-live continuation if product rules permit.

### Tool timeout

Fallback:

-   retry only when safe;
-   use idempotency;
-   reconcile state before repeating side effects.

### Safety uncertainty

Fallback:

-   safest permitted path;
-   no confident unsafe recommendation.

### Core invariant

> **Ayla must fail transparently and conservatively, not confidently
> hallucinate success.**

------------------------------------------------------------------------

## 19. Recovery Manager

The runtime should preserve enough execution metadata to recover from
interruptions.

Conceptual state:

``` text
last_successful_state
current_interaction
pending_action
tool_calls
idempotency_keys
correlation_id
```

Examples:

### App closed during C03

On return:

> "We stopped at one short clarification. Continue?"

Do not restart onboarding unnecessarily.

### Booking request timed out

Do not blindly issue another create request.

First reconcile the previous operation using the idempotency/correlation
identifier.

------------------------------------------------------------------------

## 20. Observability

Every decision should be traceable without requiring raw
chain-of-thought.

Record structured telemetry such as:

``` text
conversation_id
turn_id
decision_id
intent
active_goal
facts_used
missing_slots
policy_decision
next_action
tools_requested
tool_results/status
recommendation_id
fallback_reason
latency
model/version
prompt/config version
```

We need to answer:

> "Why did Ayla ask this question?"

and:

> "Why did this recommendation happen?"

from structured provenance, not from hidden model reasoning.

------------------------------------------------------------------------

## 21. Conversation scenario suite

Do not implement thousands of conversation branches.

Instead build a growing **behavioral evaluation suite** that tests one
universal runtime.

Initial MVP target: roughly **50--100 high-value canonical scenarios**,
later growing from real pilot failures and edge cases.

Scenario classes:

1.  happy paths;
2.  conversation deviations;
3.  memory/context;
4.  safety;
5.  transactional failures;
6.  AI uncertainty;
7.  adversarial/chaotic input.

Examples:

``` text
GIVEN recent surgery is known
WHEN user asks for a procedure recommendation
THEN ordinary recommendation cannot bypass safety handling
```

``` text
GIVEN training_frequency is already known and current
WHEN C03 needs activity context
THEN Ayla must not ask the same question again unnecessarily
```

``` text
GIVEN booking creation timed out
WHEN user repeats “book me”
THEN runtime reconciles the previous request before attempting another create
```

The suite tests **invariants**, not exact wording.

------------------------------------------------------------------------

## 22. Core behavioral invariants

At minimum:

``` text
Never recommend when policy says blocked.

Never claim a booking is confirmed without authoritative confirmation.

Never invent live availability.

Never persist protected/sensitive facts outside the required consent/policy flow.

Never silently ignore a user's correction.

Never repeatedly ask for an already-known, sufficiently fresh fact without reason.

Never hide material uncertainty when context is insufficient.

Never allow LLM output alone to perform an irreversible action.

Never let commercial ranking silently override user-fit recommendation.

Always preserve recommendation provenance through fulfillment where technically possible.

Always provide a controlled fallback when a critical dependency fails.
```

------------------------------------------------------------------------

## 23. Repository responsibility model to preserve

The agent must verify the exact current repository contracts, but the
intended separation is:

### `ayla-knowledge`

Canonical product/semantic/policy knowledge:

-   product meaning;
-   intent semantics;
-   safety canon;
-   memory semantics;
-   recommendation semantics;
-   cross-domain rules.

### `ayla-ai-core`

Shared AI primitives:

-   prompt composition;
-   structured schemas;
-   common response rendering;
-   reusable AI-domain primitives;
-   confidence-aware language behavior.

It should not become the owner of backend truth or direct database
access.

### `ai-bot-platform`

Runtime/orchestration:

-   conversation runtime;
-   context retrieval/projection;
-   decision orchestration;
-   model routing;
-   tool registry/dispatch;
-   retries;
-   recovery;
-   fallbacks;
-   observability.

### Transactional backend (`beautygo_backend` or current authoritative backend)

Source of truth for:

-   users;
-   durable facts;
-   goals where canonical persistence belongs;
-   consent;
-   specialists;
-   services;
-   availability;
-   bookings;
-   reviews;
-   transactional state.

**Important:** the refinement agent must compare this intended model
with the actual current repository responsibility matrix and report
conflicts rather than silently changing ownership.

------------------------------------------------------------------------

## 24. What must NOT happen

### Anti-pattern 1 --- giant dialogue tree

``` text
if goal == ...
  if answer == ...
    if ...
```

This will become unmaintainable.

### Anti-pattern 2 --- LLM as application server

The model must not independently own transactions, memory and policy.

### Anti-pattern 3 --- all history in every prompt

Creates cost, latency, privacy and consistency problems.

### Anti-pattern 4 --- collecting every possible fact

Only acquire context required for a current user benefit.

### Anti-pattern 5 --- recommendation as opaque AI text

Recommendation must have structured output, provenance and explanation.

### Anti-pattern 6 --- UI owns business branching

Mobile/web UI renders runtime state. It should not independently
duplicate the recommendation/safety decision graph.

### Anti-pattern 7 --- "AI confidence" as a substitute for policy

Confidence never overrides hard safety/business constraints.

------------------------------------------------------------------------

## 25. MVP implementation strategy

Do not build the entire future Ayla platform before Controlled Pilot.

Recommended order:

### Phase A --- Contracts

Define:

-   `DecisionContext`;
-   `NextAction`;
-   interaction states;
-   question registry schema;
-   context fact/proposal schema;
-   recommendation output schema;
-   safety decision schema;
-   tool result envelope;
-   error/fallback taxonomy.

### Phase B --- C01--C03 orchestration

Implement:

-   broad goal;
-   desired outcome;
-   missing-context calculation;
-   adaptive question selection;
-   known-context reuse;
-   context summary;
-   correction flow.

### Phase C --- Safety

Implement:

-   safety signal/state;
-   required safety questions;
-   allowed/restricted/blocked result;
-   conservative fallback.

### Phase D --- C04--C05

Implement:

-   Next Best Action;
-   explanation;
-   recommendation provenance;
-   procedure mapping;
-   alternatives/rejection;
-   no-eligible state.

### Phase E --- Existing fulfillment integration

Pass canonical identifiers into existing:

-   specialist ranking;
-   service detail;
-   availability;
-   booking.

Maintain `recommendation_id`.

### Phase F --- Recovery + observability

Add:

-   idempotency;
-   reconciliation;
-   resumable interaction;
-   structured traces;
-   fallback reasons.

### Phase G --- Evaluation

Create the first canonical scenario suite and make it a release gate.

------------------------------------------------------------------------

## 26. What the refinement agent must do

The next agent must **not treat this document as canonical truth without
verification**.

It should:

1.  inspect the current repositories and canonical knowledge;
2.  compare this target architecture against what is already
    implemented;
3.  identify reusable components;
4.  identify conflicts with existing ADRs/contracts;
5.  distinguish:
    -   already implemented;
    -   partially implemented;
    -   missing;
    -   obsolete;
    -   conflicting;
6.  propose the smallest migration path toward the target architecture;
7.  avoid rebuilding working fulfillment/recommendation components
    unnecessarily;
8.  define exact repository ownership for every new component;
9.  define API/contracts before implementation;
10. propose P0/P1/P2 priorities for Controlled Pilot.

The agent should especially verify:

-   current Conversation Model boundaries;
-   current Intent contract;
-   current Memory pipeline;
-   current Recommendation engine and `ScoreBreakdown`;
-   current tool registry and backend tools;
-   safety ownership and existing policies;
-   existing goal-related routes/models;
-   current C01--C05 implementation status;
-   current specialist ranking;
-   recommendation attribution support;
-   retry/idempotency behavior;
-   observability/tracing;
-   existing scenario/evaluation tests.

------------------------------------------------------------------------

## 27. Questions the refinement agent must answer

1.  Where should `DecisionOrchestrator` live?
2.  Which existing runtime component is closest to it?
3.  Do we need a graph library, or is a small internal decision graph
    sufficient for MVP?
4.  What is the canonical `DecisionContext` schema?
5.  What is the canonical `NextAction` schema?
6.  How are required/missing context fields declared?
7.  Where does the Question Registry live?
8.  Who owns question semantics versus UI copy?
9.  What exactly can the LLM infer automatically?
10. Which inferences require confirmation?
11. Which facts may become durable memory?
12. What consent is required for each fact class?
13. How is fact freshness handled?
14. What is the exact safety gate before C04/C05?
15. What is the structured recommendation output?
16. How does C04 map to C05?
17. How does C05 hand off to the existing specialist ranking?
18. How is `recommendation_id` propagated to appointment/visit/feedback?
19. What happens when no eligible recommendation exists?
20. What happens when context is insufficient?
21. What happens when model/tool/memory/recommendation services fail?
22. Which operations require idempotency/reconciliation?
23. What structured telemetry is required?
24. What are the first 50--100 release-gating scenarios?
25. Which current components can be reused unchanged?

------------------------------------------------------------------------

## 28. Desired end state

The desired behavior is not that Ayla "always knows the answer".

The desired behavior is:

``` text
If Ayla knows
→ she acts confidently within policy.

If Ayla is missing something important
→ she asks the smallest useful question.

If she already knows it
→ she does not ask again unnecessarily.

If the user corrects her
→ she updates the working context.

If facts conflict
→ she resolves or asks.

If safety is uncertain
→ she becomes more conservative.

If a backend/tool fails
→ she does not fabricate success.

If a recommendation is made
→ she can explain what facts influenced it.

If the user rejects it
→ she learns the reason and offers an allowed alternative.

If the conversation is interrupted
→ she can resume.

If the model changes
→ behavioral scenario tests prove that Ayla still behaves correctly.
```

That combination --- not a single prompt or model --- is what should
make Ayla feel **sane, understanding, intelligent and trustworthy**.

------------------------------------------------------------------------

## 29. Architectural maxim

> **Ayla should be conversational on the outside and structured on the
> inside.**

The flexibility comes from language understanding and adaptive
orchestration.

The reliability comes from contracts, authoritative state, policy,
safety, validation, idempotency, provenance and tests.

The intelligence comes from combining both.
