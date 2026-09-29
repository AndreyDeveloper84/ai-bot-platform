# Ayla Product Principles — Amendment Plan v0.1 → v0.2

**Document type:** Canon amendment plan  
**Target:** `Ayla Product Principles v0.1` → `v0.2`  
**Status:** Draft for owner/canon review  
**Change mode:** Amendment, not full rewrite

## 1. Цель

Провести новые owner-approved Product Principles через существующий canonical `Product Principles v0.1`, сохранив его governance-структуру и устранив противоречия вокруг Living Digital Twin, memory, safety, proactivity, progress и precedence.

Основания:
- OD-MVP-1 — LDT исключён из обязательного MVP critical path.
- OD-MVP-2 — Food Intelligence / Food Scanner входит в MVP как первый everyday contextual signal.
- OD-MVP-3 — Memory Foundation входит в MVP с начала; persistence проходит policy/consent gates.
- OD-MVP-4 — MVP hypothesis: `Goal → Signal → Context → Recommendation → Action → Memory → Progress`.

Важно: конкретный feature composition MVP остаётся собственностью MVP Scope / Release Contract, а не Product Principles.

## 2. Стратегия

Использовать четыре типа изменений:

- **KEEP** — сохранить.
- **MODIFY** — сохранить идею, изменить норматив/границы.
- **REMOVE / RELOCATE** — убрать из universal Principles и перенести canonical owner.
- **ADD** — добавить отсутствующую устойчивую норму.

Не выполнять массовый stylistic rewrite незатронутых разделов.

## 3. Frontmatter / Canon Metadata — MODIFY MINIMALLY

Сохранить canonical ownership, `node_id`, type, owner, priority, repository и security/export metadata.

Изменить версию на `0.2` и дату `updated`.

`depends_on` перепроверить после upstream consistency review.

MVP Scope не делать родителем Principles.

## 4. Purpose — KEEP + MINOR MODIFY

Сохранить Principles как стабильные продуктовые нормы.

Добавить границу:

> Product Principles определяют устойчивые правила принятия продуктовых решений, но не конкретный feature composition отдельного релиза.

## 5. Position in Canon — KEEP

Сохранить существующую структуру:

```text
Ayla Constitution — hard constraints / side anchor
Ayla Product Essence — highest product source
├── LDT Manifesto
├── Product Vision
├── Product Thesis
└── Product Principles
      ↓
MVP Scope → User Journey → Domain → UX → Engineering
```

Перед канонизацией проверить, не требует ли Product Thesis v0.5 обязательный LDT в MVP hypothesis. Principles не должны самостоятельно переопределять Thesis.

## 6. How to Use These Principles — MODIFY

Сохранить contextual heuristics, Product Owner judgment и explicit escalation.

Заменить универсальное положение «между принципами нет формального порядка» на ограниченный **Hard Precedence Layer**:

```text
1. Safety / legal hard constraints
2. User autonomy and consent
3. Expected user outcome
4. Engagement optimization
5. Commercial optimization
```

После этого слоя остальные продуктовые эвристики остаются contextual и могут требовать owner judgment.

## 7. Human First — KEEP

Сохранить полностью. Особенно норму, что человек — главный герой, а Twin, Ayla, memory и booking — средства.

## 8. Goal at the Center — KEEP + STRENGTHEN

Сохранить Transformation Goal как центральную сущность.

Добавить:

> The user defines the goal. Ayla may help clarify it but must not silently substitute its own definition of success.

Связать с goal-relative progress:

```text
User-defined Goal → Allowed Signals → Context → Progress interpretation
```

## 9. The Path Is Visible — MODIFY SUBSTANTIALLY

Убрать обязательную связь видимости пути с Living Digital Twin.

Целевой норматив:

> В ключевых сценариях человек должен понимать своё текущее состояние, цель, значимые изменения и движение к цели. Living Digital Twin может быть одним из способов представления пути, но не является обязательным механизмом первого MVP или каждого этапа продукта.

Decision tests:
- понимает ли пользователь положение относительно цели;
- видит ли значимые изменения;
- честно ли representation разделяет факт/оценку/прогноз;
- не зависит ли ценность искусственно от одной representation technology.

Добавить anti-pattern: LDT как обязательный gateway к пользовательской ценности.

## 10. Identity Continuity — REMOVE FROM UNIVERSAL CORE / RELOCATE

Identity continuity остаётся важным LDT invariant, но после OD-MVP-1 перестаёт быть универсальным принципом всей Ayla.

Перенести основной норматив в `Ayla Living Digital Twin Manifesto` или LDT-specific specification.

В Principles допустима conditional норма:

> Если используется persistent personal representation, включая LDT, она должна сохранять identity continuity и не подменять пользователя другим образом.

## 11. Honest Representation — KEEP + EXTEND

Сохранить разделение fact / reconstruction / estimate / prediction / goal.

Добавить:

> Observation ≠ Confirmed Fact.  
> AI Inference ≠ User Fact.

Для Food Intelligence:

> Распознавание еды/поведения является observation/model output и не становится автоматически подтверждённым или медицинским фактом.

## 12. Memory as Continuity — EXPAND / RENAME

Переименовать в **Progressive Memory + User Control**.

Зафиксировать lifecycle:

```text
Current Interaction
        ↓
Working Context
        ↓
Useful Memory Candidate
        ↓
Policy / Consent Gate
        ↓
Persistent Memory
```

Invariants:
- session context ≠ persistent memory;
- observation ≠ confirmed fact;
- AI inference ≠ user fact;
- persistent memory проходит policy/consent gate;
- sensitive information имеет более строгий режим.

Права пользователя:
- **See**
- **Correct**
- **Forget**
- **Disable**

Если memory materially влияет на recommendation, Ayla должна уметь объяснить релевантный remembered context.

## 13. Contextual Proactivity — ADD CORE PRINCIPLE

Поднять proactivity из trade-off в самостоятельную норму.

> Ayla may act proactively only when there is a meaningful, explainable and goal-relevant signal.

```text
Signal
  ↓
Observation
  ↓
Recommendation Candidate
  ↓
Relevance / Timing / Safety Gate
  ↓
Optional Proactive Interaction
```

Правила:
- observation ≠ notification;
- recommendation candidate ≠ notification;
- frequency/DAU не являются целью;
- пользователь контролирует proactive behavior;
- при сомнении предпочтительна тишина.

Short form: **Useful when needed, quiet when not.**

## 14. Explainable Next Step — KEEP + STRENGTHEN AGENCY

Сохранить explainability и realistic next step.

Добавить:
- Ayla может рекомендовать primary option;
- финальное решение всегда у пользователя;
- alternatives доступны по запросу, отказу, недоступности primary или близкой релевантности;
- rejection = signal, not failure;
- запрещено давление и повторное навязывание.

Short form: **Recommend, explain, never coerce.**

## 15. Booking Is Downstream — KEEP

Усилить:

> Booking is an action capability, not the product outcome.

Booking — один из вариантов `Action` в общем loop:

`Goal → Signal → Context → Recommendation → Action → Memory → Progress`.

## 16. User in Control — KEEP + GENERALIZE

Расширить контроль с Twin/data на:
- data;
- memory;
- personalization;
- recommendations;
- proactive behavior;
- personal representations, включая Twin.

Сохранить consent/revocation/deletion invariants.

## 17. Progress Without Pressure — KEEP + EXPAND

Сохранить dignity / anti-shaming.

Добавить:

> Progress is evaluated relative to the user's own goal, not a universal score invented by Ayla.

```text
User-defined Goal → Permitted Signals → Understandable Progress
```

Запретить универсальные допущения:
- lower weight = always progress;
- more bookings = progress;
- more activity = progress;
- more usage = progress.

Ayla может уточнить vague goal, но не незаметно заменить его собственной success metric.

## 18. Value on Both Sides — KEEP + STRENGTHEN

Добавить ranking invariant:

```text
Organic Relevance ≠ Commercial Value
```

Не могут скрыто повышать organic ranking:
- commission;
- provider subscription tier;
- promotional budget;
- advertising spend;
- platform margin;
- commercial partnership value.

Paid placement — только отдельная, явно маркированная поверхность.

Допустимые neutral tie-breakers: availability, distance, user price, preferences, quality/reliability, probability of completion.

## 19. Safety & Wellness Boundary — ADD CORE PRINCIPLE

Добавить явную норму:

> Ayla is a wellness product, not a medical diagnostic system.

Ayla может работать с everyday patterns: food, hydration, sleep, activity, recovery, beauty/wellness routines, habits, non-medical self-care.

Не должна:
- диагностировать;
- выводить заболевания из обычных behavioral signals;
- выдавать wellness observation за medical fact;
- заменять professional care;
- превращать uncertain inference в persistent medical truth.

Hard invariants:

> Food observation ≠ medical fact.  
> Inference ≠ diagnosis.

## 20. User Outcome First — ADD CORE PRINCIPLE

Добавить явный optimization target:

> Ayla optimizes progress toward the user's goal, not engagement, number of bookings, time in product or revenue from a specific recommendation.

Корректной рекомендацией могут быть отдых, изменение routine, бесплатное действие или отсутствие действия.

Hard rule:

> Commercial or engagement optimization must never override safety, autonomy or expected user benefit.

## 21. Product Restraint / Release Discipline — ADD GOVERNANCE PRINCIPLE

Зафиксировать устойчивую норму:

> Feature must create clear user value now or materially improve Ayla's ability to help later.

Для текущего release:

> После фиксации release scope новая функциональность входит только если закрывает подтверждённый critical-path, safety или release blocker.

Decision questions:
1. Какую user problem решает capability?
2. Как улучшает движение к goal?
3. Почему нужна сейчас?

Не записывать здесь конкретные решения «Food Scanner in» или «Twin out»: это MVP Scope.

## 22. Decision Test — REBUILD

Обновить общий test:

1. Помогает ли решение двигаться к self-defined goal?
2. Сохраняет ли user agency?
3. Находится ли внутри safety/wellness boundary?
4. Используется ли memory корректно и прозрачно?
5. Объяснима ли meaningful recommendation?
6. Свободен ли organic ranking от hidden commercial influence?
7. Основана ли proactivity на meaningful signal и уместном timing?
8. Показывается ли progress относительно user goal?
9. Создаёт ли feature value сейчас или materially improves future help?
10. Принадлежит ли изменение текущему release scope?

№10 — governance/admission question, а не вечная характеристика продукта.

## 23. Tensions and Trade-offs — MODIFY

Новая conflict model:

```text
Safety / Legal
      ↓
Autonomy / Consent
      ↓
Expected User Outcome
      ↓
Engagement
      ↓
Commercial Outcome

Then:
contextual product heuristics
      ↓
explicit trade-off
      ↓
Product Owner escalation when unresolved
```

Сохранить:
- personalization vs minimization;
- proactive help vs relevance;
- business value vs neutrality;
- progress visibility vs dignity.

`Twin fidelity vs user effort` заменить на более универсальное `Representation fidelity vs user effort` либо перенести в LDT context.

## 24. Non-goals — KEEP + ADD

Добавить:

> Product Principles do not define the exact feature composition or Wave sequencing of a specific MVP release.

## 25. Relationship to Canonical Sources — MODIFY

Сохранить таблицу ownership.

Уточнить LDT Manifesto: canonical sibling / aligning input для LDT, но его LDT-specific нормы не автоматически являются mandatory requirements первого MVP.

Уточнить MVP Scope:

> Downstream owner of concrete release composition, mandatory capabilities, release gates and Wave sequencing.

Product Thesis перепроверить на Twin-specific MVP claims.

## 26. Open Questions — ADD CANONIZATION DEPENDENCIES

**OQ-P1 — Product Thesis alignment:** нужен ли amendment Thesis v0.5 из-за старой LDT-dependent MVP hypothesis?

**OQ-P2 — LDT Manifesto relationship:** требуется ли amendment Manifesto или достаточно оставить его LDT-specific sibling?

**OQ-P3 — Principle numbering:** после relocation Identity Continuity и добавления новых principles решить перенумерацию. Рекомендация — чистая нумерация v0.2 + mapping в Change Log.

## 27. Concrete MVP Composition — KEEP OUT OF PRINCIPLES

Не включать как вечные Principles:
- LDT не требуется для первого MVP;
- Food Scanner входит в MVP;
- конкретную Wave sequence;
- конкретный набор capabilities;
- pilot release gates.

Ownership:

```text
Product Principles
        ↓
Owner Decisions
        ↓
MVP Scope / Release Contract
        ↓
MVP Roadmap
        ↓
User Journey / Domain / UX / Engineering
```

## 28. Mapping v0.1 → v0.2

| v0.1 element | Action | v0.2 target |
|---|---|---|
| Human First | KEEP | Human First |
| Goal at the Center | KEEP + strengthen | Goal at the Center |
| The Path Is Visible | MODIFY | Representation-neutral visible path |
| Identity Continuity | RELOCATE | LDT-specific / conditional invariant |
| Honest Representation | KEEP + extend | Honest Representation + observation/inference rules |
| Memory as Continuity | EXPAND | Progressive Memory + User Control |
| Explainable Next Step | KEEP + strengthen | Explainability & User Agency |
| Booking Is Downstream | KEEP | Downstream Action |
| User in Control | KEEP + generalize | Data/memory/personalization/proactivity control |
| Progress Without Pressure | KEEP + expand | Dignity + Goal-relative Progress |
| Value on Both Sides | KEEP + strengthen | Economic Neutrality & Trust |
| Proactive help trade-off | PROMOTE | Contextual Proactivity |
| Wellness boundary | ADD | Safety & Wellness Boundary |
| User outcome optimization | ADD | User Outcome First |
| Release restraint | ADD | Product Restraint / Release Discipline |
| No formal precedence | MODIFY | Limited hard precedence + contextual heuristics |

## 29. Canonization Sequence

1. **Upstream consistency check:** Product Essence, Vision, Thesis, LDT Manifesto.
2. **Resolve upstream contradictions:** только минимальные amendments.
3. **Author Product Principles v0.2:** по этому plan, без переписывания unaffected sections.
4. **Consistency review:** Constitution, Essence, Vision, Thesis, Manifesto, Decision Log.
5. **Owner approval:** зафиксировать Principles v0.2.
6. **Update MVP Scope:** провести конкретный release composition — Twin out, Food Intelligence in, Memory Foundation in, новый MVP validation loop.
7. **Update Roadmap + User Journey:** только после Scope.

## 30. Non-goals этого amendment

Не:
- проектировать Food Scanner;
- определять Food recognition architecture;
- проектировать memory storage;
- определять consent UX;
- переписывать LDT Manifesto целиком;
- проектировать Wave 2 engineering backlog;
- добавлять release features;
- менять business model;
- менять booking architecture;
- превращать Principles в MVP requirements document.

## 31. Acceptance Criteria

v0.2 готов к канонизации, если:

1. Нет universal requirement использовать LDT для видимости пути.
2. LDT-specific identity continuity не является universal MVP invariant.
3. Goal остаётся user-defined и центральным.
4. User Outcome First выражен явно.
5. Contextual Proactivity выражена явно.
6. Memory имеет progressive lifecycle + user-control boundary.
7. Observation / inference / confirmed fact разделены.
8. Safety & Wellness Boundary выражена явно.
9. Food/wellness signals не становятся автоматически medical facts.
10. Explainability сохранена.
11. Booking остаётся downstream action.
12. Economic neutrality имеет explicit ranking invariant.
13. Progress определяется относительно user goal.
14. Hard precedence разрешает safety/autonomy/outcome/commercial conflicts.
15. Concrete MVP feature composition отсутствует в Principles.
16. Governance и relationship-to-canon v0.1 сохранены.
17. Не создаётся второй конкурирующий Product Principles document.
18. Upstream contradictions устранены или зарегистрированы как canonization blockers.

## 32. Target Conceptual Set

```text
Human First
    +
User-Defined Goal
    +
User Outcome First
    +
Visible / Understandable Path
    +
Honest Representation
    +
Progressive Memory + User Control
    +
Contextual Proactivity
    +
Explainability + Agency
    +
Safety & Wellness Boundary
    +
Booking Downstream
    +
Goal-Relative Progress Without Pressure
    +
Economic Neutrality
    +
Product Restraint
```

Это conceptual set, не обязательная финальная нумерация.

## 33. Final Amendment Ruling

`Ayla Product Principles v0.1` не заменяется новым независимым документом.

Правильная операция:

> **Amend v0.1 → v0.2, сохранив canonical/governance skeleton v0.1 и проведя через него новые owner-approved нормы.**

Главные substantive изменения:

1. отвязать universal product path от обязательного Living Digital Twin;
2. перенести LDT-specific identity continuity в его canonical owner;
3. сделать User Outcome First явным optimization principle;
4. превратить memory в progressive, consent-aware lifecycle;
5. поднять Contextual Proactivity в core principle;
6. добавить explicit Safety & Wellness Boundary;
7. определить progress относительно user-defined goal;
8. усилить Economic Neutrality;
9. ввести ограниченный hard precedence;
10. сохранить конкретный MVP composition за MVP Scope / Release Contract.

**Next gate:** upstream consistency review перед authoring/canonization `Ayla Product Principles v0.2`.
