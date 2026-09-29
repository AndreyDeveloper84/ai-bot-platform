Ты работаешь над Ayla Product Brain v1.

ТЕКУЩИЙ ЭТАП
G2 — Goal / Wellness / Plan Authority Reconciliation.

Это MEASURE-FIRST architecture gate.

НЕ проектируй Plan Engine.
НЕ пиши код.
НЕ делай миграции.
НЕ меняй схемы.
НЕ создавай PR с реализацией.
НЕ обновляй Linear.
НЕ исправляй найденные проблемы.
НЕ пытайся привести код к контрактам.

Твоя задача — сначала установить, ЧТО ФАКТИЧЕСКИ ПОСТРОЕНО на текущих canonical heads, где находятся пересекающиеся authority boundaries и какие owner decisions действительно нужны перед Goal → Plan design.

==================================================
0. ПОЧЕМУ ЭТОТ GATE НУЖЕН
==================================================

Measure-first разбор Goal Candidate обнаружил две параллельные области.

Первая:

    goals.ClientGoal

Это текущая authoritative модель пользовательской цели.

Вторая:

    wellness.DesiredOutcome
    wellness.PersonalPlan
    wellness.PlanOutcomeLink
    wellness.PlanAction
    wellness.ProgressObservation
    wellness.EvidenceRegistryEntry

wellness зарегистрирован в INSTALLED_APPS, подключён к URL и содержит, среди прочего, target_date/horizon semantics и модель 0..N DesiredOutcome.

При этом предварительный замер установил:

- writers для wellness domain не найдены;
- goal_intention_gate для processing сейчас всегда отказывает;
- ClientGoal и wellness существуют рядом;
- target_date отсутствует в ClientGoal, но существует в PlanOutcomeLink;
- multiple outcomes уже предусмотрены в wellness;
- отдельный будущий Plan contract также существует в документах.

Если сейчас просто реализовать Plan Engine по документу, можно получить:

    ClientGoal
    +
    DesiredOutcome
    +
    Plan outcome semantics

как три пересекающихся authority model.

Этого допустить нельзя.

==================================================
1. УЖЕ ПРИНЯТЫЕ OWNER DECISIONS — НЕ ПЕРЕОТКРЫВАТЬ
==================================================

Следующие решения FINAL для этого gate.

1. Goal — user-owned durable desired outcome.

Goal НЕ является:
- CurrentNeed;
- Service;
- TenantOffer;
- Recommendation;
- Booking;
- Plan.

2. Multiple ACTIVE Goals разрешены.

Существующий:

    clientgoal_one_active_per_client

— schema-level contradiction с каноном.

Продуктовое решение НЕ переоткрывать.

3. ACTIVE != CURRENT.

Не существует глобальной семантики:

    current_goal

Текущий decision context должен отдельно определять relevant goals.

4. Current explicit user intent сильнее любого ранее сохранённого Goal.

5. Complex Goal не дробится автоматически на child Goals.

Например:

    "Через два месяца свадьба, хочу привести себя в порядок"

может оставаться одной Goal.

Разложение на лицо / тело / волосы / процедуры относится к Plan, а не к автоматическому созданию нескольких Goals.

6. Completed Booking НЕ доказывает Goal ACHIEVED.

7. LLM может извлечь GoalCandidate.

LLM НЕ может создать authoritative Goal.

8. Curated GoalOption click является достаточным explicit user action для Goal creation.

Отдельный confirm-screen после явного клика НЕ обязателен.

9. Conversational/free-text GoalCandidate НЕ может auto-persist.

Путь:

    UserEvent
      → GoalCandidate
      → controlled promotion policy
      → explicit user action
      → GoalCreateCommand
      → Goal

10. Для одного UserEvent наружу в promotion lifecycle допускается максимум ОДИН promotable GoalCandidate.

Внутренний extractor может иметь несколько semantic hypotheses.

Ambiguity не означает создание нескольких GoalCandidates.

11. Existing marketplace `_match_goal_keys()` НЕ объявлять GoalCandidate authority.

Это существующий discovery matcher.

Он может быть reuse input после отдельного анализа, но сегодня его семантика:

    query → goal keys → marketplace filtering

а не:

    user → durable GoalCandidate.

12. Goal identity и user wording различаются.

Нельзя подделывать user wording текстом чипа.

Пример curated click:

    goal_key = relax
    selection_label_snapshot = "Расслабиться"
    user_wording = null
    origin = CURATED_OPTION_CLICK

Пример user text:

    "Хочу меньше уставать после работы"

может дать:

    goal_key = recharge
    user_wording = exact original text
    origin = USER_TEXT

если controlled mapping действительно это установил.

13. Goal lifecycle target:

    ACTIVE
    PAUSED
    ACHIEVED
    ARCHIVED

Freshness — отдельная ось, не lifecycle:

    FRESH
    NEEDS_RECONFIRMATION
    ...

14. Legacy:

    is_active=False

НЕ разрешено автоматически трактовать как ARCHIVED или ACHIEVED.

Историческая система не сохраняла причину.

Нужна migration-safe legacy semantics без придумывания прошлого user intent.

15. Пользователь должен иметь возможность прекратить Goal без выбора новой.

Нужны controlled lifecycle actions, а не только "создать следующую".

16. `known.goal` как canonical scalar должен исчезнуть.

Target direction:

    known.active_goals[]

и отдельно:

    relevant_goal_refs[]

для конкретного decision.

Нельзя вводить:

    active_goals[0] == current_goal

как новую скрытую семантику.

17. `area` и `feeling` текущей Goal anketa удаляются из P0 Goal flow.

Причина уже измерена:
- значения не имеют runtime consumers;
- не влияют на final GoalOptions;
- не едут в decision context;
- не используются analytics/admin;
- пользователь платит UX cost без decision value.

Не исследуй заново, "может быть они всё-таки полезны".
Это решение принято.

18. Target date сейчас НЕ добавляем в ClientGoal.

Сначала требуется настоящий authority reconciliation с wellness domain.

19. Wellness gate НЕ разрешено просто включить.

Сначала нужно понять семантику и authority существующих моделей.

20. Goal Relevance Resolver не меняет lifecycle Goals.

Он только определяет relevance для конкретного decision.

==================================================
2. MEASUREMENT DISCIPLINE
==================================================

Работай по правилу:

    MEASURE
      ↓
    CLASSIFY
      ↓
    GAP
      ↓
    OWNER DECISION ONLY IF NECESSARY

Не начинай с чтения handoff/spec как истины runtime.

Сначала current canonical code.

Документы используются только ПОСЛЕ measurement для сравнения:

    runtime reality
        vs
    accepted canon / spec target

Каждый существенный вывод должен иметь:

1. repository;
2. exact SHA;
3. file;
4. line/function/model;
5. фактическое поведение;
6. классификацию.

Используй:

    EXISTS
    PARTIAL
    MISSING
    CONTRADICTS_CANON
    STALE_SPEC
    UNKNOWN_NOT_MEASURED

Не превращай UNKNOWN в MISSING.

Отсутствие замера != отсутствие реализации.

Если какой-либо repo/branch невозможно достоверно измерить — напиши это в "Не замерено".

Не используй старые pilot numbers как текущие без нового замера, если их freshness window истёк.

==================================================
3. РЕПОЗИТОРИИ
==================================================

Минимум проверить:

1. djangoproject-catalog
2. ai-bot-platform
3. ayla-ai-core
4. ayla-knowledge — только если фактически участвует в Goal/Plan semantics

Для каждого сначала:

    git fetch
    git rev-parse <canonical branch>

Не угадывай canonical branch.

Предыдущий measurement обнаружил, что у ayla-ai-core `origin/dev` не существовал.

Если ситуация сохранилась:
- установи реальную canonical branch из repository evidence;
- если однозначно установить нельзя — UNKNOWN_NOT_MEASURED;
- не grep локальную случайную ветку как production truth.

==================================================
4. ОСНОВНОЙ ВОПРОС G2
==================================================

Нужно доказательно ответить:

    Что является authoritative durable representation
    пользовательского desired outcome сегодня?

и затем:

    Что ДОЛЖНО остаться authoritative после Goal → Plan implementation,
    учитывая уже существующие ClientGoal и wellness models?

Вторую часть НЕ решай сам, если measurement не делает ответ однозначным.

Если существуют несколько правдоподобных authority migrations — принеси owner decision.

==================================================
5. TRACK A — ClientGoal
==================================================

Проследи ClientGoal полностью.

Нужно установить:

- model fields;
- constraints;
- migrations;
- writers;
- readers;
- events;
- serializers/manual payload builders;
- decision-context representation;
- ownership;
- source/provenance;
- lifecycle semantics;
- goal key/text semantics;
- relationships с GoalOption;
- relationships с GoalOptionCategory;
- relationships с recommendation;
- relationships с nutrition;
- relationships с wellness;
- relationships с analytics;
- relationships с Plan, если есть.

Особенно:

A1.
Есть ли кроме ClientGoal другие runtime authoritative Goal entities?

A2.
ClientGoal используется как:
- desired outcome;
- selected preference;
- recommendation filter;
- UI state;
- analytics event;
- всё сразу?

A3.
Какие consumers считают ClientGoal authoritative truth?

A4.
Какие поля/семантики невозможно мигрировать без потери provenance?

A5.
Прочитай реальные migrations ClientGoal, а не только их имена.

==================================================
6. TRACK B — wellness.DesiredOutcome
==================================================

Разбери `wellness.DesiredOutcome` построчно и по всем consumers.

Нужно установить:

- поля;
- constraints;
- statuses;
- ownership;
- provenance;
- timestamps;
- cardinality;
- links;
- writers;
- readers;
- API;
- service layer;
- admission gates;
- feature flags;
- tests;
- admin;
- analytics;
- serializers;
- event emission;
- whether it is reachable in live runtime.

Ответь отдельно:

B1.
Что по смыслу DesiredOutcome?

B2.
Это duplicate Goal, Plan outcome, internal projection или отдельная сущность?

Не отвечай по имени класса.
Докажи поведением и relationships.

B3.
Кто способен его создать сегодня?

B4.
Кто способен изменить lifecycle?

B5.
Кто читает его и зачем?

B6.
Может ли существовать DesiredOutcome без ClientGoal?

B7.
Есть ли mapping:

    ClientGoal ↔ DesiredOutcome

Если да — где authority?
Если нет — прямо зафиксируй.

B8.
Есть ли код, который предполагает, что DesiredOutcome уже canonical Goal?

==================================================
7. TRACK C — PersonalPlan
==================================================

Разбери `wellness.PersonalPlan`.

Установи:

- lifecycle;
- cardinality;
- relation к user/client;
- relation к DesiredOutcome;
- relation к PlanOutcomeLink;
- relation к PlanAction;
- writer/readers;
- admission;
- status transitions;
- versioning/revision semantics;
- provenance;
- APIs;
- tests;
- analytics;
- actual runtime reachability.

Особенно проверь уже замеченное ограничение:

    0..1 ACTIVE PersonalPlan per user

Не объявляй его конфликтом автоматически.

Multiple ACTIVE Goals != multiple ACTIVE Plans.

Нужно установить, соответствует ли это уже принятому Plan contract или является старым предположением.

==================================================
8. TRACK D — PlanOutcomeLink
==================================================

Это особенно важно.

Установи точную семантику:

    PersonalPlan
       ↔
    PlanOutcomeLink
       ↔
    DesiredOutcome

Проверь:

- cardinality;
- statuses;
- target_date;
- horizon_status;
- constraints;
- timestamps;
- lifecycle;
- writer/readers;
- uniqueness;
- provenance;
- source of target date;
- кто имеет право менять target date;
- является ли target_date свойством Goal, Plan или relation Goal↔Plan по фактическому коду.

Не выводи семантику из названия поля.

Нужен фактический ответ.

Отдельно:

D1.
Может ли один DesiredOutcome иметь разные target_date в разных Plan revisions/Plans?

D2.
Может ли один Plan обслуживать несколько DesiredOutcome?

D3.
Может ли Goal существовать без Plan?

D4.
Может ли target_date существовать без Plan?

D5.
Что происходит после elapsed target date?

D6.
Меняет ли elapsed lifecycle Goal/Outcome или это только computed freshness/horizon signal?

==================================================
9. TRACK E — PlanAction
==================================================

Нужно установить, что такое существующий PlanAction.

Не предполагай, что:

    PlanAction == будущий PlanStep

Проверь:

- fields;
- action types;
- links;
- ordering;
- status;
- execution;
- service/capability references;
- tenant/provider references;
- booking references;
- recommendation references;
- completion;
- provenance;
- writers/readers.

После measurement классифицируй:

    SAME_SEMANTICS_AS_PLAN_STEP
    PARTIAL_OVERLAP
    DIFFERENT_ENTITY
    UNKNOWN

с evidence.

==================================================
10. TRACK F — ProgressObservation / EvidenceRegistryEntry
==================================================

Проверь эти сущности, потому что они могут скрыто превращать Goal в progress-tracking system.

Установи:

- что является evidence;
- кто пишет;
- кто читает;
- что считается progress;
- может ли observation изменить Goal lifecycle;
- может ли booking completion считаться Goal progress;
- может ли оно автоматически сделать Goal ACHIEVED;
- существует ли LLM inference;
- существует ли provenance;
- существуют ли thresholds;
- используется ли всё это runtime.

Канон уже говорит:

    completed booking != Goal achieved

Если runtime делает иначе — CONTRADICTS_CANON.

==================================================
11. TRACK G — wellness gates
==================================================

Полностью разберись, почему wellness writers заперты.

Предыдущий measurement нашёл:

    goal_intention_gate
    purpose=processing
    → always deny

Нужно установить:

- это намеренный product freeze?
- privacy gate?
- consent gate?
- unfinished stub?
- dead code?
- rollout guard?
- какой accepted decision/ADR/commit его ввёл?
- что именно он блокирует?
- какие read paths остаются разрешены?
- какие write paths физически недостижимы?
- можно ли gate включить configuration flag или требуется код?
- какие prerequisites проверяются?

НЕ включать gate.

НЕ предлагать "просто удалить return false".

==================================================
12. TRACK H — overlap matrix
==================================================

Построй таблицу:

| Semantic responsibility | ClientGoal | DesiredOutcome | PersonalPlan | PlanOutcomeLink | PlanAction | Contract target |
|---|---|---|---|---|---|---|
| desired outcome identity | | | | | | |
| user wording | | | | | | |
| canonical outcome key | | | | | | |
| lifecycle | | | | | | |
| freshness | | | | | | |
| target date | | | | | | |
| provenance | | | | | | |
| multiple goals | | | | | | |
| plan identity | | | | | | |
| plan revision | | | | | | |
| step/action | | | | | | |
| progress | | | | | | |
| evidence | | | | | | |
| recommendation link | | | | | | |
| booking link | | | | | | |

В ячейке не ставь просто yes/no.

Используй:

    AUTHORITATIVE
    PROJECTION
    PARTIAL
    DECLARED_UNUSED
    UNREACHABLE
    ABSENT
    UNKNOWN

и ссылку на evidence.

Цель таблицы — увидеть overlapping authority.

==================================================
13. TRACK I — end-to-end reachability
==================================================

Проследи отдельно реальные пути:

PATH 1
    User selects Goal in Mini App
      → ?
      → ClientGoal
      → ?
      → recommendation

PATH 2
    User states goal in MAX DM
      → ?
      → discovery matcher
      → ?
      → durable Goal?

PATH 3
    ClientGoal exists
      → ?
      → DesiredOutcome?

PATH 4
    DesiredOutcome exists
      → ?
      → PersonalPlan?

PATH 5
    PersonalPlan exists
      → ?
      → PlanAction?

PATH 6
    PlanAction
      → ?
      → Recommendation?

PATH 7
    Recommendation
      → ?
      → PendingBookingIntent
      → Booking

PATH 8
    Booking completed
      → ?
      → ProgressObservation?
      → Goal/DesiredOutcome lifecycle?

Для каждого edge:

    EXISTS
    PARTIAL
    MISSING
    BLOCKED_BY_GATE
    UNKNOWN

Никаких подразумеваемых стрелок.

==================================================
14. TRACK J — contract reconciliation
==================================================

Только ПОСЛЕ measurement прочитай и сравни:

- GOAL_CANDIDATE_CONTRACT_v1.0
- PLAN_ENGINE_CONTRACT_v1.0
- PLAN_ENGINE_DEPENDENCY_MAP
- PLANNING_CONSTRAINTS_CONTRACT_v1.0
- RECOMMENDATION_RESOLVER_CONTRACT_v1.0
- связанные accepted owner decisions / registry entries
- wellness design docs, если они существуют

Для каждого существенного расхождения:

    RUNTIME:
    SPEC:
    CLASS:
    CONSEQUENCE:

Классы:

    RUNTIME_BEHIND_CANON
    RUNTIME_AHEAD_OF_SPEC
    STALE_SPEC
    TRUE_CONTRADICTION
    PARALLEL_MODEL
    TERMINOLOGY_COLLISION
    UNKNOWN

Не называй документ canonical только потому, что он лежит в docs/.

Проверь его статус и accepted decisions.

==================================================
15. SAFETY SEAM
==================================================

Safety Architecture v1 уже FINAL FREEZE и передана отдельному implementation track.

Но этот gate обязан обнаруживать integration seam.

Правило:

Feature/domain owner отвечает за обнаружение required safety integration.

Safety owner отвечает за canonical Safety Policy/Engine.

Этот агент НЕ создаёт собственную safety policy.

Если Goal/Wellness/Plan code:
- интерпретирует health data;
- использует sensitive evidence;
- принимает решения о допустимости;
- создаёт health-derived Goals;
- автоматически меняет Plan из health signals;

зафиксируй integration point.

Не проектируй новую safety matrix.

==================================================
16. ЧТО НЕ СЧИТАТЬ OWNER DECISION
==================================================

Не приноси владельцу вопросы, которые уже разрешаются:

- принятым каноном;
- фактом runtime;
- обычной безопасной migration mechanics;
- backward-compatible API evolution;
- очевидным удалением stale comments;
- тестовой реализацией;
- названием поля/класса.

Owner decision нужен только если есть минимум два продуктово различающихся допустимых target semantics и существующий канон не выбирает между ними.

==================================================
17. ОСОБО ОПАСНЫЕ ЛОЖНЫЕ ВЫВОДЫ
==================================================

Запрещено делать такие выводы без evidence:

"DesiredOutcome существует → он canonical Goal."

"ClientGoal старее → его надо удалить."

"wellness новее → он правильнее."

"target_date находится в PlanOutcomeLink → дата принадлежит Plan."

"PersonalPlan один → пользователь может иметь только одну Goal."

"PlanAction называется Action → это PlanStep."

"writers не найдены → модели dead."

"gate всегда deny → код не нужен."

"GoalOption → DesiredOutcome автоматически."

"Booking completed → progress."

"elapsed target_date → Goal achieved."

==================================================
18. ОЖИДАЕМЫЙ АРТЕФАКТ
==================================================

Создай:

    docs/MEASUREMENT_GOAL_WELLNESS_PLAN_AUTHORITY.md

Структура:

1. Measurement bases
2. Executive verdict
3. ClientGoal
4. DesiredOutcome
5. PersonalPlan
6. PlanOutcomeLink
7. PlanAction
8. ProgressObservation / EvidenceRegistryEntry
9. Wellness gates
10. Authority overlap matrix
11. End-to-end reachability
12. Canon/spec reconciliation
13. Confirmed contradictions
14. Migration hazards
15. Safety integration seams
16. Owner decisions required
17. What is NOT an owner decision
18. What was not measured
19. Exact reproduction commands

В Executive verdict дай короткую сводку:

    EXISTS x
    PARTIAL x
    MISSING x
    BLOCKED x
    PARALLEL_MODEL x
    CONTRADICTS_CANON x
    UNKNOWN x

==================================================
19. ФОРМАТ OWNER DECISIONS
==================================================

Если решения действительно нужны, для каждого:

    OD-GWP-1 — <точный вопрос>

    Runtime evidence:
    ...

    Existing canon:
    ...

    Why canon does not already answer:
    ...

    Option A:
    ...

    Option B:
    ...

    Consequence A:
    ...

    Consequence B:
    ...

    Blocks:
    ...

Не рекомендуй вариант, пока measurement не завершён.

Не раздувай список.
Если вопрос можно закрыть evidence — закрой evidence.

==================================================
20. DEFINITION OF DONE
==================================================

Gate measurement завершён только если мы можем доказательно нарисовать:

    User Goal
        ↓
    authoritative representation
        ↓
    optional Plan
        ↓
    PlanStep / equivalent

без двух competing sources of truth.

Мы должны точно знать:

- является ли ClientGoal legacy representation или будущей Goal authority;
- является ли DesiredOutcome Goal, projection или другая сущность;
- зачем существуют обе модели;
- где должен жить target horizon;
- что такое PersonalPlan;
- что такое PlanAction;
- какие wellness entities реально достижимы;
- какие только построены;
- какие gates их блокируют;
- какие consumers зависят от каждой модели;
- какие миграции будут destructive;
- какие вопросы действительно требуют владельца.

STOP после measurement.

Не проектируй G3.
Не реализуй.
Не открывай новый architecture track.

Верни мне:
1. путь к measurement-файлу;
2. SHA баз замера;
3. executive verdict;
4. список подтверждённых contradictions;
5. owner decisions — только оставшиеся;
6. честный список "не замерено".