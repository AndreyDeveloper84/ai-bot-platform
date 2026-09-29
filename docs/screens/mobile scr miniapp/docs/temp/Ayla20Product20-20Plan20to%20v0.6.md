# Ayla MVP Product Thesis — Amendment Plan v0.5 → v0.6

**Document type:** Canon amendment plan  
**Target document:** `Ayla MVP Product Thesis v0.5`  
**Target version:** `Ayla MVP Product Thesis v0.6`  
**Status:** Draft for owner/canon review  
**Change mode:** Minimal structural amendment, not full rewrite  
**Purpose:** Провести owner-approved MVP direction через существующий Product Thesis, устранив обязательную зависимость MVP hypothesis от Living Digital Twin и сохранив сильные части Thesis: testability, validation phases, success/failure logic, admission criteria, unit economics and owner-governed validation.

---

## 1. Основание amendment

Текущий `Ayla MVP Product Thesis v0.5` остаётся правильным по роли: он должен владеть **проверяемой MVP-гипотезой**, а не точным release composition.

При этом v0.5 нормативно связывает проверку основной MVP hypothesis с Living Digital Twin. После новых owner decisions это создаёт прямой upstream-конфликт.

### Принятые owner decisions

| ID | Решение |
|---|---|
| OD-MVP-1 | Living Digital Twin исключён из обязательного MVP critical path и перенесён в Post-MVP / Later Validation |
| OD-MVP-2 | Food Intelligence / Food Scanner входит в MVP как первый everyday contextual signal |
| OD-MVP-3 | Memory Foundation входит в MVP с начала; persistent memory вводится через policy/consent gates |
| OD-MVP-4 | MVP hypothesis проверяет цикл `Goal → Signal → Context → Recommendation → Action → Memory → Progress` |

### Важная ownership-граница

Product Thesis не должен фиксировать конкретный feature implementation первого release.

Поэтому:
- `Food Intelligence` может быть указан как **пример/первый реализуемый signal**, но не должен становиться вечной фундаментальной частью Thesis;
- точный release scope должен оставаться в `Ayla MVP Scope and Release Contract`;
- Roadmap должен оставаться downstream от Scope.

---

# 2. Amendment strategy

Использовать четыре типа изменений:

- **KEEP** — сохранить раздел или норматив без существенных изменений;
- **MODIFY** — сохранить смысл, но обновить wording / scope;
- **REMOVE / RELOCATE** — убрать из основной MVP hypothesis и перенести в более подходящий owner;
- **ADD** — добавить недостающую гипотезу или validation rule.

Не переписывать весь Thesis стилистически.

Цель — изменить только то, что реально конфликтует с OD-MVP-1…4.

---

# 3. Section-by-section map

| Thesis v0.5 section | Action | v0.6 direction |
|---|---|---|
| §1 Purpose | KEEP | Thesis остаётся владельцем testable MVP hypothesis |
| §2 Scope | KEEP | Validation hypothesis / success / failure / admission |
| §3 Non-goals | KEEP | Exact release composition остаётся в MVP Scope |
| §4.1 Canonical frame | MODIFY | Убрать обязательный LDT из обязательного frame |
| §4.2 AYLA-DEC-0002 | KEEP + CLARIFY | Food = равноправный trigger/example, не вечное ядро |
| §4.3 MVP hypothesis | MAJOR MODIFY | Заменить LDT-dependent composite hypothesis |
| §5 Target Segments | KEEP | Не требует изменения |
| §6 What's In | MODIFY | Убрать mandatory LDT viability; добавить contextual signal capability |
| §7 What's Out | KEEP + CHECK | Проверить ссылки после новой hypothesis |
| §8 Admission Criteria | MODIFY | Новый admission frame без LDT-specific mandatory contribution |
| §8.1 Success Criteria | MAJOR MODIFY | Убрать Twin recognition/photo-repeat как обязательное доказательство Thesis |
| §8.2 Failure Conditions | MAJOR MODIFY | Убрать Twin failure как автоматическое опровержение всей MVP hypothesis |
| §8.3 MVP Principles | MODIFY | Убрать LDT-specific universal gates; добавить новые cross-product gates |
| §8.4 Validation Gate | KEEP + ADAPT | Сохранить разделение Phase 1 / Phase 2 |
| §9 Dependencies | UPDATE | После amendment обновить ссылки |
| §10 Open Questions | KEEP + ADD | Добавить только реальные unresolved dependencies |
| §11 Risks | KEEP + REWORD | Сохранить риск mechanical checklist и расширить scope-creep risk |

---

# 4. §1 Purpose — KEEP

Сохранить роль Thesis:

> Product Thesis определяет проверяемую гипотезу MVP, сигналы её подтверждения/опровержения и рамку validation.

Не превращать Thesis в:
- feature list;
- roadmap;
- engineering plan;
- release checklist.

---

# 5. §2 Scope — KEEP

Сохранить владение:

- testable MVP hypothesis;
- validation logic;
- success/failure signals;
- admission criteria;
- validation phases;
- product risks;
- Product Owner validation authority.

---

# 6. §3 Non-goals — KEEP + STRENGTHEN

Сохранить разделение ownership.

Добавить explicit boundary:

> Exact mandatory feature composition of the MVP belongs to `Ayla MVP Scope and Release Contract`, not to Product Thesis.

Это особенно важно после OD-MVP-2, чтобы Food Scanner не оказался жёстко зашит в вечную Thesis.

---

# 7. §4.1 Canonical Frame — MODIFY

## Current conflict

v0.5 включает Living Digital Twin как обязательный визуальный/продуктовый элемент ключевого пути.

После OD-MVP-1 это больше не universal MVP condition.

## Target frame

Сформулировать product frame технологически нейтрально:

```text
Human
  ↓
Transformation Goal
  ↓
Relevant Context
  ↓
Ayla Orchestration
  ↓
Explainable Recommendation
  ↓
Realistic Action
  ↓
Continuity / Memory
  ↓
Goal-relative Progress
```

### Normative direction

- Human remains the hero.
- Transformation Goal remains central.
- Ayla remains the orchestrator.
- Memory provides continuity.
- Recommendation provides next-step guidance.
- Action converts insight into reality.
- Progress closes the loop.
- Living Digital Twin becomes optional/conditional capability that may strengthen visibility or continuity but is not mandatory for first MVP validation.

---

# 8. §4.2 AYLA-DEC-0002 — KEEP + CLARIFY

Сохранить сильную существующую норму:

> Food → beauty/wellness — один из равноправных trigger-сценариев, не архитектурный центр продукта.

Сохранить также:

> Moat Ayla — не booking сам по себе, а accumulated explainable understanding + continuity + orchestration + progress.

### Modify

Если moat в текущем тексте зависит от обязательной долгоживущей Twin-модели, изменить на:

> LDT may strengthen the moat but is not required for the first proof of the product thesis.

### Important

Не превращать Food в вечную thesis dependency.

Food — concrete MVP implementation of `Signal`, а не identity продукта.

---

# 9. §4.3 MVP Hypothesis — MAJOR MODIFY

## Remove current dependency

Убрать обязательную composite hypothesis вида:

```text
Goal
+
Living Digital Twin
+
Ayla orchestration
+
Memory continuity
+
Visible progress
+
Real actions
```

## Replace with target hypothesis

> **Если Ayla помогает пользователю сформулировать личную Transformation Goal, понимать релевантные повседневные сигналы в контексте этой цели, использовать разрешённую память для continuity, предлагать объяснимый следующий шаг, превращать его в реалистичное действие и затем показывать понятный прогресс относительно цели, то пользователь получает повторяемую персональную ценность и возвращается к Ayla ради продолжения собственного пути, а не ради отдельной функции.**

### Target loop

```text
Goal
  ↓
Signal
  ↓
Context
  ↓
Explainable Recommendation
  ↓
Realistic Action
  ↓
Memory Continuity
  ↓
Goal-relative Progress
  ↺
```

### Important

Food Intelligence — первый concrete signal в MVP Scope, но Thesis должна оставаться signal-agnostic.

---

# 10. §5 Target Segments — KEEP

Не менять сегменты только из-за изменения MVP hypothesis.

Проверить только:
- не определены ли segment-specific criteria через обязательную LDT capture;
- если да — заменить на capability-neutral validation criteria.

---

# 11. §6 What's In — MODIFY

## Remove as mandatory thesis-level item

Удалить обязательный блок `Living Digital Twin viability`, если он требует:

- baseline;
- photo fixation;
- recognition;
- repeat capture;
- identity continuity;
- correction flow;

как условие доказательства основной Thesis.

## Add instead

### Everyday Contextual Signal Viability

Пилот должен доказать, что Ayla способна:

1. получить реальный пользовательский signal;
2. интерпретировать его в контексте Goal;
3. отделить observation от confirmed fact;
4. использовать signal для relevant recommendation;
5. сохранить допустимую continuity;
6. не переходить safety/privacy boundary.

### Note

Конкретный signal (`Food Intelligence`) определяется MVP Scope.

---

# 12. §7 What's Out — KEEP + VERIFY

Сохранить existing exclusions, если они не зависят от старой LDT hypothesis.

Проверить, что:
- LDT не остаётся одновременно `mandatory in Thesis` и `deferred elsewhere`;
- Food Scanner не остаётся ошибочно полностью deferred, если Scope его поднимет;
- persistent full personalization platform по-прежнему не становится required для первого validation.

---

# 13. §8 Admission Criteria — MODIFY

## Current issue

Capability admission в MVP сейчас учитывает вклад в LDT viability как один из обязательных thesis elements.

## Target admission dimensions

Capability может быть admitted, если она существенно усиливает хотя бы один из элементов:

```text
Transformation Goal
Context / Signal Understanding
Memory Continuity
Ayla Orchestration
Explainable Recommendation
Realistic Action
Goal-relative Progress
Safety / User Agency
```

### Product restraint

Добавить:

> Capability не входит в MVP только потому, что она полезна, интересна или технически готова.

Admission должен отвечать:

1. Какую часть testable hypothesis она помогает доказать?
2. Почему эта capability нужна именно в текущем validation stage?
3. Какой evidence она должна дать?

---

# 14. §8.1 Success Criteria — MAJOR MODIFY

## Remove as universal success conditions

Не использовать как обязательное доказательство всей MVP Thesis:

- user recognizes themselves in Twin;
- repeat photo fixation;
- Twin correction success;
- identity continuity metrics;
- recognition quality as global MVP success condition.

Эти критерии могут жить в LDT-specific validation later.

## Target success groups

### S1 — Goal & Context

- пользователь формулирует/принимает Transformation Goal;
- последующие signals интерпретируются в контексте Goal;
- пользователь понимает связь между контекстом и recommendation.

### S2 — Recommendation & Action

- рекомендации воспринимаются как relevant и explainable;
- recommendation ведёт к realistic next action;
- action не обязательно является booking;
- часть пользователей действительно выполняет proposed next action.

### S3 — Continuity

- повторное взаимодействие не начинается с нуля;
- разрешённый context materially улучшает следующий interaction;
- memory не создаёт ощущение surveillance;
- user control / consent boundary работает.

### S4 — Progress & Return

- пользователь понимает движение относительно своей цели;
- progress не является artificial engagement metric;
- возникает естественная причина вернуться к Ayla;
- value повторяется не только в первом session.

### S5 — Trust & Safety

- recommendations не требуют скрытого commercial manipulation;
- health/wellness boundary соблюдается;
- пользователь сохраняет agency.

---

# 15. §8.2 Failure Conditions — MAJOR MODIFY

## Remove from global thesis failure

Не считать автоматическим опровержением всей MVP Thesis:

- Twin not recognized;
- identity drift;
- refusal to repeat photo capture;
- weak LDT fidelity.

Это становится LDT-specific failure, если/когда LDT проходит отдельную validation.

## Target thesis failure conditions

MVP hypothesis считается не подтверждённой / materially weakened, если:

1. Goal не влияет на дальнейшие recommendations/actions.
2. Everyday context не улучшает relevance.
3. Recommendation остаётся generic.
4. Explainability не создаёт доверия/понимания.
5. Action layer не приводит к realistic user action.
6. Memory не создаёт continuity.
7. Persistent context вызывает confusion/privacy distrust.
8. Пользователь не видит понятного progress.
9. Нет естественной причины вернуться.
10. Пользовательская ценность сводится только к одной функции (например, booking или food recognition).
11. Commercial incentives искажают personal recommendation.
12. Safety boundary систематически нарушается.

---

# 16. §8.3 MVP Principles — MODIFY

## Remove as universal MVP gates

LDT-specific gates:
- mandatory Twin recognition;
- identity preservation as universal release criterion;
- Twin correction flow as universal requirement;
- LDT Manifesto rules that apply only if LDT is enabled.

## Keep / strengthen

- explainability;
- user control;
- economic neutrality;
- honest representation;
- cognitive load discipline;
- dignity / no-shaming;
- recommendation relevance;
- booking downstream;
- scope discipline.

## Add

- User Outcome First;
- Contextual Proactivity;
- Progressive Memory;
- Observation ≠ Fact;
- Inference ≠ Diagnosis;
- Safety & Wellness Boundary;
- Goal-relative Progress;
- Product Restraint.

### Important

Thesis may reference Product Principles v0.2 after canonization rather than duplicating all wording.

---

# 17. §8.4 Validation Gate — KEEP + ADAPT

Сохранить сильную validation architecture:

```text
Phase 1 Release Readiness
≠
Product Thesis Validation

Phase 2 Activation Evidence
≠
Automatic Thesis Validation
```

### Preserve

- release readiness does not equal thesis validation;
- owner closes thesis validation;
- evidence must accumulate over real users;
- memory-dependent claims require Phase 2 evidence;
- killer moment and unit economics are not replaced by technical readiness.

### Adapt

Заменить LDT-dependent evidence на evidence по новому loop:

```text
Goal
→ Signal
→ Context
→ Recommendation
→ Action
→ Continuity
→ Progress
```

---

# 18. §9 Dependencies — UPDATE

После amendment проверить и обновить:

- Product Essence;
- Product Vision;
- Product Principles v0.2;
- LDT Manifesto;
- MVP Scope;
- User Journey;
- Decision Log.

### Important

Product Thesis не должен зависеть от конкретного MVP Roadmap implementation.

---

# 19. §10 Open Questions — KEEP + ADD ONLY IF NEEDED

Не превращать раздел в dumping ground.

Допустимые новые open questions:

### OQ-T1 — Signal generalization

Нужно ли Thesis формально определять класс `Everyday Signal`, либо достаточно MVP Scope / Domain?

### OQ-T2 — Progress validation

Какой минимальный evidence доказывает, что пользователь действительно воспринимает progress как meaningful, а не как decorative analytics?

### OQ-T3 — Natural return

Как отделить полезный return от искусственного engagement?

Эти вопросы не должны блокировать v0.6, если на них есть safe directional answer.

---

# 20. §11 Risks — KEEP + REWORD

Сохранить existing risks, особенно:

- mechanical feature checklist;
- premature interpretation of pilot evidence;
- overfitting to one cohort;
- mistaking technical readiness for product validation.

Добавить:

### R-T1 — Feature-center regression

Риск заменить Twin-first на Food-first как новый самостоятельный центр продукта.

Mitigation:
> Goal + context + recommendation + action + continuity + progress remain the product loop; Food is only first signal implementation.

### R-T2 — Scope creep

Риск объявлять каждую полезную capability обязательной для proof of Thesis.

Mitigation:
> admission requires direct contribution to testable hypothesis.

### R-T3 — Memory overreach

Риск путать useful context с justification for unlimited persistence.

Mitigation:
> progressive memory + consent/policy gate.

### R-T4 — Engagement substitution

Риск считать DAU, messages or bookings proof of user outcome.

Mitigation:
> progress/return evidence must remain goal-relative and user-value based.

---

# 21. What moves out of Thesis

Следующие concrete items не должны быть normative core of Product Thesis v0.6:

- Food Scanner is mandatory in Wave X;
- exact mobile screens;
- exact memory storage architecture;
- exact release gate checklist;
- exact provider list;
- exact LDT implementation;
- exact number of reminders/notifications;
- engineering backlog.

Ownership:

```text
Product Thesis
      ↓
Product Principles
      ↓
MVP Scope / Release Contract
      ↓
Roadmap
      ↓
User Journey / Domain / UX / Engineering
```

---

# 22. LDT after amendment

Living Digital Twin остаётся:

- canonical product concept;
- possible progress/identity representation;
- later validation capability;
- possible differentiator/moat enhancer.

Но не является:

- mandatory first-MVP critical path;
- mandatory proof of whole Product Thesis;
- universal success/failure condition.

### Conditional rule

> Если LDT включён в конкретный release/experiment, его recognition, identity continuity, honesty and user-control criteria remain governed by LDT-specific canonical documents.

---

# 23. Food Intelligence after amendment

Food Intelligence:

- не становится identity продукта;
- не становится вечным canonical center Thesis;
- является first MVP implementation of `Signal`;
- даёт immediate value + context;
- должен подчиняться Observation ≠ Fact и wellness boundary.

Exact Food scope belongs to MVP Scope.

---

# 24. Memory after amendment

Memory остаётся частью moat/continuity thesis, но:

```text
Working Context
        ↓
Memory Candidate
        ↓
Policy / Consent
        ↓
Persistent Memory
```

Thesis должна проверять **continuity value**, а не количество сохранённых facts.

Failure:
> memory exists technically but does not improve user experience.

Success:
> user does not need to restart their story from zero, and context improves next-step quality.

---

# 25. Progress after amendment

Progress становится thesis-level outcome:

> The user understands movement relative to their own goal.

Не использовать universal metric.

Не считать автоматически progress:
- more bookings;
- more app usage;
- lower weight;
- more scans.

Progress evidence must be goal-relative.

---

# 26. Updated MVP Hypothesis — canonical candidate wording

Предлагаемый текст для authoring v0.6:

> **Ayla создаёт устойчивую пользовательскую ценность, если помогает человеку сформулировать собственную Transformation Goal, понимать релевантные повседневные сигналы в контексте этой цели, использовать разрешённую память для continuity, предлагать объяснимый и уместный следующий шаг, превращать его в реалистичное действие и затем помогать человеку видеть понятный прогресс относительно собственной цели. Если этот цикл создаёт естественную причину возвращаться к Ayla ради продолжения личного пути, а не ради отдельной функции, основная MVP-гипотеза получает подтверждение.**

---

# 27. Updated validation loop

```text
Transformation Goal
        ↓
Everyday Signal
        ↓
Context Understanding
        ↓
Explainable Recommendation
        ↓
Realistic Action
        ↓
Memory Continuity
        ↓
Goal-relative Progress
        ↓
Natural Return
        ↺
```

### Thesis validates the loop, not one feature.

---

# 28. Acceptance criteria for Thesis v0.6

Amendment готов к канонизации, если:

1. LDT больше не является обязательной частью центральной MVP hypothesis.
2. Thesis остаётся testable, а не превращается в vision statement.
3. Goal остаётся user-defined.
4. Signal/context становятся частью hypothesis без привязки к одной feature.
5. Food не становится вечным центром Thesis.
6. Memory проверяется через continuity, а не data volume.
7. Recommendation остаётся explainable and relevant.
8. Action остаётся realistic and optional; booking не является product outcome.
9. Progress определяется относительно user goal.
10. Return оценивается как continuation of value, not engagement for engagement's sake.
11. Twin-specific success/failure criteria удалены из global thesis validation.
12. LDT-specific quality remains governed conditionally by LDT docs.
13. Phase 1 / Phase 2 validation model сохранена.
14. Unit economics / killer moment / owner closure сохранены.
15. Exact MVP feature composition остаётся в MVP Scope.
16. No direct conflict remains with Product Principles v0.2.
17. No unresolved upstream contradiction remains with Product Essence / Vision.
18. Downstream MVP Scope can be updated mechanically after Thesis v0.6.

---

# 29. Mapping v0.5 → v0.6

| v0.5 concept | Action | v0.6 target |
|---|---|---|
| Transformation Goal | KEEP | User-defined central goal |
| Living Digital Twin as mandatory hypothesis element | REMOVE from core | Optional / later capability |
| Ayla orchestration | KEEP | Contextual orchestrator |
| Memory continuity | KEEP + clarify | Progressive memory / continuity |
| Visible progress via Twin | MODIFY | Goal-relative progress, representation-neutral |
| Real actions | KEEP | Realistic action layer |
| Food trigger | KEEP + clarify | First concrete signal in Scope, not thesis center |
| Twin recognition success | RELOCATE | LDT-specific validation |
| Twin identity drift failure | RELOCATE | LDT-specific validation |
| Phase 1/2 validation | KEEP | Updated evidence loop |
| Unit economics | KEEP | Business viability dimension |
| Killer moment | KEEP | Product validation dimension |
| Product Owner closure | KEEP | Governance authority |

---

# 30. Canonization sequence

1. **Confirm Product Essence / Vision do not mandate LDT as universal first-MVP dependency.**
2. **Author Thesis v0.6 from this amendment plan.**
3. **Run consistency review against Essence, Vision, LDT Manifesto, Constitution, Decision Log.**
4. **Owner approval / canonization Thesis v0.6.**
5. **Author Product Principles v0.2** using its amendment plan and updated Thesis.
6. **Update MVP Scope / Release Contract** with actual feature composition:
   - Twin out of mandatory MVP;
   - Food Intelligence in;
   - Memory Foundation in;
   - new validation loop.
7. **Update Roadmap / User Journey.**

---

# 31. Non-goals of this amendment

Этот amendment не должен:

- переписывать Product Vision;
- проектировать Food Scanner;
- проектировать memory architecture;
- определять exact MVP cohort sizes;
- менять booking architecture;
- определять notification strategy;
- проектировать LDT v2;
- менять business model;
- превращать Thesis в release checklist;
- добавлять новые capabilities beyond owner-approved direction.

---

# 32. Final amendment ruling

`Ayla MVP Product Thesis v0.5` не требует полного переписывания.

Правильная операция:

> **Amend v0.5 → v0.6, сохранив testable thesis structure и заменив обязательную LDT-dependent hypothesis на representation-neutral value loop.**

Главные изменения:

1. убрать LDT из обязательного proof of whole MVP hypothesis;
2. заменить feature-centric proof на loop-centric validation;
3. ввести Signal / Context как явные элементы hypothesis;
4. сохранить memory как continuity mechanism;
5. сделать progress goal-relative;
6. оставить Food конкретной implementation of Signal на уровне MVP Scope;
7. сохранить Phase 1/2, killer moment, unit economics и Product Owner closure;
8. перенести Twin recognition / identity criteria в LDT-specific validation.

**Next gate:** authoring / consistency review `Ayla MVP Product Thesis v0.6`.
