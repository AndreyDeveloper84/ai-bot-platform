Ты работаешь над Ayla Product Brain v1.

ТЕКУЩИЙ НЕЗАВИСИМЫЙ ТРЕК:
G6 — CanonicalService ↔ TenantOffer Mapping Pipeline.

Этот трек идёт ПАРАЛЛЕЛЬНО с:
G2 — Goal / Wellness / Plan Authority Reconciliation.

НЕ вмешивайся в G2.
НЕ проектируй Goal/Plan.
НЕ меняй Goal contracts.

==================================================
0. РЕЖИМ РАБОТЫ
==================================================

Это MEASURE-FIRST architecture gate.

На первом проходе:

НЕ писать production code.
НЕ делать migrations.
НЕ менять mapping statuses.
НЕ выставлять VERIFIED.
НЕ исправлять услуги салонов.
НЕ запускать массовое сопоставление.
НЕ создавать mappings руками.
НЕ менять Recommendation Resolver.
НЕ ослаблять fail-closed.
НЕ превращать REVIEW_REQUIRED в VERIFIED.
НЕ менять каталог ради улучшения match rate.
НЕ обновлять Linear.
НЕ принимать owner decisions самостоятельно.

Сначала нужно установить runtime reality.

После measurement STOP.

Верни владельцу только:
- факты;
- gaps;
- contradictions;
- migration hazards;
- действительно необходимые owner decisions.

==================================================
1. ЗАЧЕМ НУЖЕН ЭТОТ GATE
==================================================

Canonical product model:

Need / Outcome
      ↓
Capability
      ↓
CanonicalService
      ↓
TenantOffer
      ↓
Recommendation
      ↓
Booking

Между:

    CanonicalService
          ↓
    TenantOffer

должен существовать доказуемый semantic mapping.

Последний pilot measurement показал:

    review_required = 206
    unmapped        = 59
    verified        = 0

formula-tela:

    unmapped = 58
    review_required = 0

Но ВАЖНО:

из этих чисел НЕЛЬЗЯ делать вывод:

    "matcher не узнал formula-tela"

Замер установил другое:

206 review_required в demo salons были созданы seed-механизмом
из собственной fixture.

Поле suggested_template фактически никто не вычислял.

Следовательно предварительная гипотеза:

    production mapping mechanism может отсутствовать вообще.

Это нужно ДОКАЗАТЬ или ОПРОВЕРГНУТЬ кодом/runtime.

==================================================
2. УЖЕ ПРИНЯТЫЙ CANON — НЕ ПЕРЕОТКРЫВАТЬ
==================================================

Модель:

    Need / Outcome
          ↓
    Capability
          ↓
    CanonicalService
          ↓
    TenantOffer

НЕ схлопывать эти уровни.

Service != TenantOffer.

TenantOffer — коммерческое предложение конкретного tenant:
- price;
- duration;
- active;
- bookability;
- local naming;
- provider availability;
- и другие tenant-specific execution facts.

CanonicalService — canonical semantic identity услуги.

Capability — то, что услуга способна обеспечить/поддержать в controlled semantic model.

==================================================
3. MAPPING STATUS CANON
==================================================

Canonical statuses:

    VERIFIED
    REVIEW_REQUIRED
    UNMAPPED

Их семантику не менять без owner decision.

VERIFIED:

существует достаточное controlled evidence, чтобы система могла
авторитетно утверждать связь:

    TenantOffer → CanonicalService

REVIEW_REQUIRED:

существует mapping hypothesis, но evidence недостаточно для authoritative use.

UNMAPPED:

authoritative semantic relation не установлена.

Критический invariant:

    REVIEW_REQUIRED != VERIFIED
    UNMAPPED != VERIFIED

Нельзя использовать:

    "похоже"
    "названия почти одинаковые"
    "LLM уверен"
    "demo fixture так сказала"

как скрытый VERIFIED.

==================================================
4. RECOMMENDATION INVARIANT
==================================================

Обычная AI semantic recommendation может использовать TenantOffer
через CanonicalService semantics только если mapping VERIFIED.

UNMAPPED offer НЕ означает:

    услуга не существует
    услуга плохая
    услуга запрещена
    услуга не bookable

UNMAPPED offer может оставаться доступным пользователю,
например при явном direct service selection.

Но Ayla не имеет права приписывать ему неизвестные semantic properties.

То же относится к REVIEW_REQUIRED.

Не исправляй:

    NO_VERIFIED_CANDIDATES

путём ослабления mapping gate.

При verified=0 этот ответ потенциально является правильным fail-closed.

==================================================
5. EVIDENCE / WHY INVARIANT
==================================================

Displayable WHY требует independent provenance.

Название поля:

    reason
    score
    match_reason
    suggested_template

само по себе НЕ создаёт evidence.

Правило:

    No displayable WHY
    → no WHY block.

Mapping evidence должен быть отдельно доказуем.

==================================================
6. LLM AUTHORITY
==================================================

LLM разрешено:

- semantic extraction;
- normalization assistance;
- candidate generation;
- controlled explanation из доказанного evidence;
- предложение mapping hypothesis.

LLM запрещено:

- самостоятельно устанавливать VERIFIED;
- становиться source of truth mapping;
- выдумывать Capability;
- выдумывать CanonicalService;
- выводить health/safety properties из названия;
- превращать similarity score в authoritative mapping;
- silently overwrite existing mapping.

LLM output максимум может стать:

    mapping candidate

или:

    REVIEW_REQUIRED

если controlled policy это допускает.

==================================================
7. MEASUREMENT DISCIPLINE
==================================================

Каждый вывод:

1. repository;
2. canonical branch;
3. exact SHA;
4. file/function/model;
5. фактическое поведение;
6. classification.

Используй:

    EXISTS
    PARTIAL
    MISSING
    CONTRADICTS_CANON
    STALE_SPEC
    DEAD_CODE
    UNREACHABLE
    UNKNOWN_NOT_MEASURED

Не превращай UNKNOWN в MISSING.

Не начинай со spec.

Порядок:

    CODE/RUNTIME
        ↓
    MEASUREMENT
        ↓
    SPEC/CANON COMPARISON
        ↓
    GAP

==================================================
8. СВЕЖЕСТЬ PILOT DATA
==================================================

Не цитируй старые pilot numbers как текущую reality без повторного замера,
если срок их годности истёк.

Пересними минимум:

- SalonService total;
- mapping_status distribution;
- distribution per tenant;
- VERIFIED count;
- REVIEW_REQUIRED count;
- UNMAPPED count;
- suggested_template populated count;
- canonical/template relation populated count;
- formula-tela distribution.

Запиши:
- timestamp;
- command;
- container;
- result.

Если нет доступа к live environment:

    UNKNOWN_NOT_REMEASURED

и используй старые числа только как historical baseline,
явно подписав дату.

==================================================
9. РЕПОЗИТОРИИ
==================================================

Проверь минимум:

1. djangoproject-catalog
2. ai-bot-platform
3. ayla-ai-core — если участвует в mapping semantics
4. ayla-knowledge — если canonical service/capability knowledge находится там

Для каждого:

    git fetch
    git rev-parse <actual canonical branch>

Не угадывай `origin/dev`.

Если canonical branch невозможно установить:

    UNKNOWN_NOT_MEASURED.

==================================================
10. TRACK A — DOMAIN MODEL
==================================================

Найди ВСЕ сущности/поля, связанные с:

- TenantOffer;
- SalonService;
- CanonicalService;
- ServiceTemplate;
- template;
- suggested_template;
- mapping_status;
- mapping source;
- mapping provenance;
- mapping version;
- mapping confidence;
- review state;
- verification;
- aliases;
- taxonomy;
- capabilities.

Не доверяй названиям.

Для каждого поля установи:

    WHO WRITES?
    WHO READS?
    WHEN?
    FROM WHAT EVIDENCE?
    IS IT AUTHORITATIVE?

Особенно:

A1.
Что фактически является TenantOffer сегодня?

A2.
Что фактически является CanonicalService сегодня?

A3.
Существует ли CanonicalService как отдельная entity?

Если нет, что играет эту роль?

A4.
Что такое template по фактическому коду?

A5.
CanonicalService и template — одно и то же или разные уровни?

A6.
Есть ли Capability как runtime entity?

Не делай вывод из документов — сначала код.

==================================================
11. TRACK B — CURRENT MAPPING FIELDS
==================================================

Полностью проследи:

    mapping_status
    suggested_template
    template / canonical relation

Для каждого:

- schema;
- constraints;
- default;
- migrations;
- writers;
- readers;
- admin;
- API;
- serializers;
- sync;
- seed;
- tests;
- Recommendation Resolver.

Ответь:

B1.
Кто может выставить UNMAPPED?

B2.
Кто может выставить REVIEW_REQUIRED?

B3.
Кто может выставить VERIFIED?

B4.
Есть ли переходы:

    UNMAPPED → REVIEW_REQUIRED
    REVIEW_REQUIRED → VERIFIED
    VERIFIED → REVIEW_REQUIRED
    VERIFIED → UNMAPPED

B5.
Кто имеет authority на каждый переход?

B6.
Есть ли audit trail?

B7.
Можно ли определить, ПОЧЕМУ mapping VERIFIED?

B8.
Можно ли определить, КТО его verified?

B9.
Можно ли определить, НА КАКОЙ ВЕРСИИ canonical catalog он verified?

==================================================
12. TRACK C — SEEDS
==================================================

Разбери ВСЕ seed paths, создающие mapping-related данные.

Особенно:

    seed_demo_salons.py

Но не ограничивайся им.

Установи:

- какие поля seed выставляет;
- откуда берётся template;
- выполняется ли matching;
- fixture знает mapping заранее;
- создаётся ли REVIEW_REQUIRED;
- создаётся ли VERIFIED;
- используются ли те же code paths, что production;
- создаёт ли seed иллюзию работающего pipeline.

Отдельно классифицируй:

    TEST/DEMO DATA GENERATION
        vs
    PRODUCTION MAPPING LOGIC

Это не одно и то же.

==================================================
13. TRACK D — MATCHER DISCOVERY
==================================================

Ищи не только слово "matcher".

Найди любые механизмы, которые потенциально делают:

    TenantOffer
        ↓
    candidate CanonicalService(s)

Ищи:

- exact name;
- normalized name;
- aliases;
- taxonomy;
- category;
- parent category;
- token matching;
- morphology;
- fuzzy matching;
- embeddings;
- vector search;
- LLM;
- rule engine;
- import mapping;
- external IDs;
- source IDs;
- manually assigned IDs;
- seed mapping;
- admin action;
- background jobs;
- management commands;
- signals;
- celery/jobs;
- sync hooks.

Для каждого найденного механизма:

    INPUT
    OUTPUT
    CALLERS
    REACHABILITY
    AUTHORITY
    STATUS PRODUCED

Не объявляй механизм production matcher,
если никто его не вызывает.

==================================================
14. TRACK E — suggested_template
==================================================

Это отдельный обязательный track.

Предыдущий measurement утверждает:

    suggested_template никто не вычисляет.

Перепроверь.

Найди:

- field definition;
- every writer;
- every reader;
- admin;
- serializer;
- sync;
- seed;
- command;
- task;
- tests.

Ответь:

E1.
Может ли production runtime автоматически заполнить suggested_template?

E2.
Если да — какой exact path?

E3.
Если нет — как реально появляются значения?

E4.
Может ли человек выставить suggested_template?

E5.
Что происходит после выставления?

E6.
Приводит ли suggested_template автоматически к REVIEW_REQUIRED?

E7.
Может ли suggested_template стать VERIFIED?

E8.
Есть ли stale suggestion invalidation после изменения offer/template?

==================================================
15. TRACK F — HUMAN REVIEW
==================================================

Проверь, существует ли настоящий review workflow.

Ищи:

- admin pages;
- queues;
- moderation;
- approve/reject actions;
- bulk actions;
- reviewer identity;
- reviewed_at;
- comment/reason;
- audit event;
- permissions;
- role restrictions.

Нужно установить:

    REVIEW_REQUIRED

— это реальный workflow state

или просто значение enum без процесса.

Проследи:

    REVIEW_REQUIRED
         ↓
    human sees candidate
         ↓
    approve/reject
         ↓
    VERIFIED / UNMAPPED

Если стрелки нет — MISSING.

==================================================
16. TRACK G — CHANGE / INVALIDATION
==================================================

Очень важно.

Даже правильный VERIFIED mapping может устареть.

Проверь, что происходит, если:

- salon renames service;
- changes category;
- changes description;
- service becomes inactive;
- canonical template changes;
- canonical taxonomy changes;
- capability assignment changes;
- catalog version changes;
- offer deleted/recreated;
- sync overwrites fields.

Ответь:

G1.
Mapping immutable или mutable?

G2.
Когда VERIFIED должен пересматриваться?

G3.
Есть ли invalidation mechanism?

G4.
Есть ли mapping_version / catalog_version?

G5.
Может ли старый VERIFIED пережить semantic change offer и остаться VERIFIED?

Если да — migration hazard.

==================================================
17. TRACK H — CANONICAL SERVICE / CAPABILITY
==================================================

Не предполагай, что этот слой уже построен.

Установи фактически:

    Capability
       ↓
    CanonicalService

Где живёт Capability?

Где живёт CanonicalService?

Кто создаёт связь?

Кто её проверяет?

Есть ли:

- semantic attributes;
- aliases;
- contraindication/safety metadata;
- planning constraints;
- category ancestry;
- service properties;
- canonical identifiers;
- versions.

Если Capability отсутствует runtime:

    MISSING

Если существует только в docs:

    DECLARED_ONLY / MISSING_RUNTIME

Если template фактически совмещает несколько ролей —
зафиксируй TERMINOLOGY_COLLISION.

==================================================
18. TRACK I — RECOMMENDATION CONSUMER
==================================================

Проследи mapping до Recommendation Resolver.

Нужно доказать:

    mapping_status
        ↓
    eligibility
        ↓
    candidate
        ↓
    recommendation

Ответь:

I1.
Где VERIFIED проверяется?

I2.
Проверяется ли вообще?

I3.
Может ли REVIEW_REQUIRED пройти?

I4.
Может ли UNMAPPED пройти?

I5.
Есть ли отдельный direct-booking path, допускающий unmapped offer?

I6.
Что возвращается при zero VERIFIED?

I7.
Есть ли silent fallback?

I8.
Есть ли LLM fallback?

I9.
Есть ли fallback на category/name similarity?

I10.
Есть ли surface divergence:
    MAX
    Mini App
    backend API?

Критический invariant:

    zero VERIFIED

не должен превращаться в invented recommendation.

==================================================
19. TRACK J — WHY / EVIDENCE
==================================================

Проследи:

    TenantOffer
       ↓
    mapping evidence
       ↓
    Recommendation evidence
       ↓
    user-facing WHY

Для каждого user-visible reason установи provenance.

Классифицируй:

    GROUNDED
    DERIVED_FROM_GROUNDED
    UNSUPPORTED
    UNKNOWN

Не считать score evidence.

Не считать LLM prose evidence.

Не считать mapping_status сам по себе explanation.

Проверь недавнее правило:

    No displayable WHY
    → no WHY block.

==================================================
20. TRACK K — IMPORT / SYNC
==================================================

Mapping pipeline особенно легко ломается на sync boundary.

Проследи:

    Ayla catalog
        ↔
    bot mirror

Установи:

- какие IDs идут через границу;
- какие mapping fields копируются;
- mapping_status;
- suggested_template;
- canonical IDs;
- template IDs;
- version;
- timestamps.

Учитывай уже обнаруженный класс дефекта:

    SpecialistProfile.id
        vs
    user_id

где обе стороны имели по 31 объекту,
но пересечение было 0.

Поэтому нужны cross-boundary probes,
а не только unit tests одной стороны.

Проверь минимум один реальный TenantOffer end-to-end:

    authoritative DB row
        ↓
    API/sync payload
        ↓
    mirror row
        ↓
    resolver input

Сверь IDs и mapping semantics.

==================================================
21. TRACK L — FORMULA-TELA SAMPLE
==================================================

Используй formula-tela как реальный case study,
но НЕ исправляй его.

Выбери небольшой репрезентативный sample,
например 5–10 услуг разных типов.

Для каждой покажи:

    local offer
    local category
    local name
    local description if any
    current mapping_status
    suggested_template
    existing canonical/template candidates
    whether deterministic evidence exists
    whether human review would be required

Цель НЕ получить высокий match rate.

Цель — проверить, хватает ли данных вообще для mapping pipeline.

Если mapping кажется очевидным человеку —
это всё равно не VERIFIED без controlled rule/provenance.

==================================================
22. TRACK M — MAPPING LIFECYCLE
==================================================

По фактическому runtime попробуй построить:

    TenantOffer created/imported
          ↓
    ?
          ↓
    MappingCandidate
          ↓
    ?
          ↓
    REVIEW_REQUIRED
          ↓
    ?
          ↓
    VERIFIED
          ↓
    Recommendation eligibility
          ↓
    ?
          ↓
    semantic change
          ↓
    revalidation/invalidation

Каждую стрелку классифицируй:

    EXISTS
    PARTIAL
    MISSING
    MANUAL_ONLY
    SEED_ONLY
    DEAD_CODE
    UNKNOWN

Никаких подразумеваемых стрелок.

==================================================
23. TRACK N — AUTHORITY MATRIX
==================================================

Построй таблицу:

| Responsibility | Backend catalog | Bot mirror | ai-core | ayla-knowledge | Admin/Human | Seed | LLM |
|---|---|---|---|---|---|---|---|
| TenantOffer truth | | | | | | | |
| CanonicalService identity | | | | | | | |
| Capability identity | | | | | | | |
| mapping candidate generation | | | | | | | |
| REVIEW_REQUIRED | | | | | | | |
| VERIFIED decision | | | | | | | |
| mapping provenance | | | | | | | |
| invalidation | | | | | | | |
| recommendation eligibility | | | | | | | |
| WHY evidence | | | | | | | |

Используй:

    AUTHORITATIVE
    CONTROLLED_POLICY
    PROJECTION
    CANDIDATE_ONLY
    PRESENTATION_ONLY
    SEED_ONLY
    ABSENT
    UNKNOWN

Нельзя иметь два AUTHORITATIVE owners одной semantic responsibility
без явного reconciliation.

==================================================
24. TRACK O — TEST REALITY
==================================================

Найди tests вокруг mapping.

Классифицируй их:

    UNIT_ONE_SIDE
    CONTRACT_ONE_FIXTURE
    CROSS_BOUNDARY
    LIVE_DATA
    GOLDEN_MAPPING
    ADMIN_WORKFLOW
    INVALIDATION

Особенно ответь:

Есть ли тест, где:

    TenantOffer

создан одной bounded context,

а:

    CanonicalService / mirror / resolver

получен через реальную другую boundary?

Если обе стороны fixture построил один test author,
это НЕ достаточное доказательство cross-system compatibility.

==================================================
25. CONTRACT RECONCILIATION
==================================================

Только после measurement сравни с:

- RECOMMENDATION_RESOLVER_CONTRACT_v1.0
- RECOMMENDATION_BOUNDARY_PLAN_v1.0
- RECOMMENDATION_PATHS_AUDIT
- PLANNING_CONSTRAINTS_CONTRACT_v1.0
- PLAN_ENGINE_CONTRACT_v1.0
- Catalog Semantic Mapping accepted decisions
- relevant ADR/decision registry
- relevant service/catalog docs

Для каждого расхождения:

    RUNTIME:
    SPEC:
    CLASS:
    CONSEQUENCE:

Классы:

    RUNTIME_BEHIND_CANON
    RUNTIME_AHEAD_OF_SPEC
    STALE_SPEC
    TRUE_CONTRADICTION
    PARALLEL_AUTHORITY
    TERMINOLOGY_COLLISION
    UNKNOWN

==================================================
26. SAFETY SEAM
==================================================

Не проектируй Safety.

Но mapping может иметь safety consequences.

Особенно:

    requires_health_check
    contraindication
    health suitability
    pregnancy
    recovery windows
    medical service semantics

Не выводить эти свойства из TenantOffer name.

Если CanonicalService mapping не VERIFIED,
нельзя наследовать safety semantics как доказанные.

Если найдёшь существующую реализацию,
которая делает:

    offer name
       ↓
    inferred health property
       ↓
    recommendation/safety decision

зафиксируй как отдельный integration gap.

Safety Architecture v1 остаётся отдельной canonical authority.

==================================================
27. OWNER DECISIONS
==================================================

После полного measurement принеси только решения,
которые нельзя вывести из уже принятого канона.

Формат:

    OD-MAP-1 — <вопрос>

    Runtime evidence:
    ...

    Existing canon:
    ...

    Why canon does not answer:
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

Не спрашивай владельца:

"Нужен ли mapping?"

Он уже нужен.

Не спрашивай:

"Можно ли REVIEW_REQUIRED рекомендовать?"

Нет — это уже закрыто.

Не спрашивай:

"Можно ли LLM сделать authority?"

Нет.

Не спрашивай:

"Можно ли при zero VERIFIED сделать fallback?"

Не через недоказанные semantic mappings.

==================================================
28. ЧТО НЕ ЯВЛЯЕТСЯ OWNER DECISION
==================================================

Не выноси владельцу:

- название таблицы;
- название Python class;
- индекс БД;
- конкретную management command;
- retry mechanics;
- batch size;
- admin pagination;
- технический способ idempotency;
- безопасную backward-compatible migration;
- удаление stale comment;
- test fixture mechanics.

Если target semantics уже определена,
это engineering decision.

==================================================
29. ОЖИДАЕМЫЙ АРТЕФАКТ
==================================================

Создай:

    docs/MEASUREMENT_CANONICAL_TENANT_MAPPING.md

Структура:

1. Measurement bases
2. Executive verdict
3. Current domain model
4. TenantOffer
5. CanonicalService / template reality
6. Capability reality
7. Mapping fields and statuses
8. All mapping writers
9. All mapping readers
10. Seed paths
11. Matcher discovery
12. suggested_template lifecycle
13. Human review workflow
14. Mapping invalidation/versioning
15. Recommendation consumption
16. WHY/evidence provenance
17. Sync/mirror boundary
18. formula-tela sample
19. End-to-end mapping lifecycle
20. Authority matrix
21. Test reality
22. Canon/spec reconciliation
23. Confirmed contradictions
24. Migration hazards
25. Safety integration seams
26. Owner decisions required
27. What is NOT an owner decision
28. What was not measured
29. Exact reproduction commands

==================================================
30. EXECUTIVE VERDICT
==================================================

В начале дай числа:

    EXISTS:
    PARTIAL:
    MISSING:
    SEED_ONLY:
    MANUAL_ONLY:
    DEAD_CODE:
    CONTRADICTS_CANON:
    UNKNOWN:

И ответь одной фразой на каждый вопрос:

1. Есть ли production mapping pipeline сегодня?
2. Есть ли automatic candidate generation?
3. Есть ли human review workflow?
4. Может ли production code получить VERIFIED?
5. Есть ли provenance VERIFIED?
6. Есть ли invalidation?
7. Есть ли Capability runtime layer?
8. Есть ли CanonicalService runtime layer?
9. Соблюдает ли Recommendation Resolver VERIFIED-only?
10. Что именно означает сегодняшнее `verified=0`?

==================================================
31. DEFINITION OF DONE
==================================================

Measurement завершён, когда можно без догадок нарисовать:

    TenantOffer
        ↓
    Mapping Candidate
        ↓
    Review / Verification
        ↓
    VERIFIED CanonicalService
        ↓
    Capability semantics
        ↓
    Recommendation eligibility

и у КАЖДОЙ стрелки известны:

- owner;
- code path;
- evidence;
- persistence;
- status;
- failure behavior.

Если стрелки нет — MISSING.

Если она только в seed — SEED_ONLY.

Если только человек вручную меняет поле —
MANUAL_ONLY.

Если документ обещает её, а runtime не имеет —
RUNTIME_BEHIND_CANON.

STOP после measurement.

НЕ проектируй implementation.
НЕ создавай matcher.
НЕ размечай 58 услуг formula-tela.
НЕ выставляй VERIFIED.
НЕ ослабляй Recommendation Resolver.

Верни владельцу:

1. путь к measurement artifact;
2. SHA всех измеренных repositories;
3. свежие pilot counts, если был доступ;
4. executive verdict;
5. end-to-end lifecycle;
6. confirmed contradictions;
7. owner decisions — только действительно необходимые;
8. список UNKNOWN / не замерено.