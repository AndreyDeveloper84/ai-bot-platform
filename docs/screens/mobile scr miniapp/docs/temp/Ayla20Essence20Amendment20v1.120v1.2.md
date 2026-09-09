# Ayla Product Essence — Amendment Plan v1.1 → v1.2

**Document type:** Canon amendment plan  
**Target document:** `Ayla Product Essence v1.1`  
**Target version:** `Ayla Product Essence v1.2`  
**Status:** Draft for owner/canon review  
**Change mode:** Targeted amendment only  
**Scope:** LDT/MVP boundary and representation neutrality  
**Purpose:** Устранить upstream-конфликт между действующим Product Essence v1.1 и owner-approved MVP direction, сохранив стратегическую роль Living Digital Twin в долгосрочном продукте, но убрав его обязательность из первого MVP critical path.

---

## 1. Основание amendment

Текущий `Ayla Product Essence v1.1` является высшим продуктовым источником и нормативно связывает ключевой пользовательский путь и первый MVP с Living Digital Twin.

После принятия owner decisions:

- **OD-MVP-1:** Living Digital Twin исключён из обязательного MVP critical path;
- **OD-MVP-2:** Food Intelligence / Food Scanner входит в MVP как первый everyday contextual signal;
- **OD-MVP-3:** Memory Foundation входит в MVP с начала;
- **OD-MVP-4:** MVP hypothesis проверяет цикл `Goal → Signal → Context → Recommendation → Action → Memory → Progress`;

возникает прямой upstream-конфликт.

Product Thesis, Product Principles и MVP Scope не должны переопределять Product Essence самостоятельно. Поэтому требуется минимальный amendment `v1.1 → v1.2`.

---

## 2. Что НЕ меняется

Этот amendment не является отказом от Living Digital Twin.

Сохраняется:

- Human-first identity продукта;
- Transformation Goal как центральная сущность;
- Ayla как оркестратор пути;
- memory/continuity как долгосрочное преимущество;
- explainable recommendations;
- realistic actions;
- visible and understandable progress;
- Living Digital Twin как стратегическая визуальная capability и возможный differentiator;
- LDT-specific honesty, identity preservation и user control в тех сценариях, где LDT используется.

Не меняется долгосрочное направление Ayla.

Меняется только одно фундаментальное утверждение:

> **Living Digital Twin больше не является обязательным промежуточным звеном для существования пользовательской ценности и обязательным proof point первого MVP.**

---

## 3. Amendment strategy

Использовать только три типа изменений:

- **KEEP** — сохранить;
- **MODIFY** — изменить только обязательность/формулировку LDT;
- **RELOCATE** — перенести release-specific LDT requirements в LDT-specific canonical owner / MVP Scope.

Не открывать заново остальные продуктовые решения Essence.

---

## 4. §1 «Что такое Ayla» — MODIFY

Сделать определение representation-neutral:

> Ayla помогает человеку сформулировать желаемое изменение, понимать свой текущий контекст, получать объяснимый следующий шаг, действовать и видеть прогресс относительно собственной цели.

Living Digital Twin определить отдельно:

> Living Digital Twin — стратегическая capability визуального представления состояния, идентичности и прогресса, которая может усиливать путь пользователя, но не является обязательным механизмом каждого ключевого сценария или первого MVP.

Сохранить human-first framing и Transformation Goal.

---

## 5. §6 «Killer Feature — Living Digital Twin» — MODIFY / RENAME

Переименовать в:

### `Living Digital Twin — Strategic Visual Capability`

Сохранить:

- возможность визуально отражать человека;
- identity continuity;
- прогресс во времени;
- correction;
- honest representation;
- эмоциональную ценность узнаваемого representation.

Убрать:

- утверждение, что Twin является обязательной точкой объединения всех доменов;
- утверждение, что пользовательская ценность Ayla невозможна без Twin;
- implication, что LDT должен быть готов раньше proof of core value loop.

Новый норматив:

> LDT должен конкурировать за место в roadmap на основании доказуемого улучшения понимания, trust, recommendation quality или progress visibility.

---

## 6. §9 «Главный визуальный интерфейс» — MODIFY

Не закреплять LDT как universal primary visual interface.

Использовать conditional framing:

> Когда Living Digital Twin включён и соответствует задаче, он может выступать primary visual representation состояния и прогресса. В остальных сценариях допустимы чат, карточки, timelines, графики, summaries и другие понятные representation layers.

Hard rule:

> Representation serves user understanding; the user does not serve the representation technology.

---

## 7. §12 «Главный продуктовый цикл» — MAJOR MODIFY

Заменить обязательный Twin-dependent flow на:

```text
Human
  ↓
Transformation Goal
  ↓
Relevant Signal
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
  ↺
```

LDT становится optional representation layer:

```text
Context / State / Progress
          ↓
   optional visual layer
          ↓
Living Digital Twin
```

Он может усиливать цикл, но не является обязательным transition.

Food не фиксировать здесь как вечную часть Essence. `Signal` должен оставаться generic.

---

## 8. §17 «Главная ценность MVP» — MAJOR MODIFY

Удалить как обязательные proof points:

- visual Twin baseline;
- mandatory photo fixation;
- mandatory recognition;
- repeated Twin capture;
- Twin-specific progress representation.

Новый MVP-level framing в Essence:

> Первый MVP должен доказать, что Ayla создаёт повторяемую персональную ценность: связывает user-defined Goal с релевантным контекстом, объяснимой рекомендацией, реалистичным действием, continuity и понятным progress.

Конкретные mandatory capabilities принадлежат `MVP Scope and Release Contract`.

---

## 9. §18 «Граница Living Digital Twin в MVP» — RELOCATE

Удалить release-specific LDT requirements из Product Essence.

Перенести их в:

- `Ayla Living Digital Twin Manifesto`;
- LDT-specific scope/spec;
- конкретный future MVP/release scope, если LDT снова будет admitted.

К relocated requirements относятся:

- photo capture;
- recognition;
- correction;
- identity continuity;
- state comparison;
- media pipeline;
- LDT-specific quality thresholds.

В Essence оставить только conditional стратегическую норму:

> Если LDT используется, он должен быть узнаваемым, честным, исправляемым и сохранять identity continuity.

---

## 10. §19 UX direction — MODIFY MINIMALLY

Сделать universal UX tests representation-neutral:

- Понимает ли пользователь своё состояние?
- Понимает ли связь между Goal, context и recommendation?
- Понятен ли следующий шаг?
- Видит ли пользователь meaningful progress?
- Если используется personal representation — узнаёт ли он себя и может ли исправить её?

---

## 11. §20 Product Center — KEEP

Сохранить:

> **Transformation Goal — центральная сущность продукта.**

Уточнить при необходимости:

> LDT — representation/capability, а не доменный центр продукта.

---

## 12. §22 Decision Test — MODIFY

Обновить LDT-dependent вопросы.

Целевой набор:

1. Усиливает ли решение путь пользователя к self-defined Goal?
2. Улучшает ли оно понимание relevant context?
3. Делает ли recommendation более explainable/relevant?
4. Помогает ли выполнить realistic action?
5. Создаёт ли continuity?
6. Делает ли progress понятнее?
7. Сохраняет ли honesty / user agency / safety?
8. Если используется LDT или другая personal representation — сохраняется ли recognizability and identity?

Удалить universal admission requirement, по которому capability должна обязательно усиливать Twin.

---

## 13. Living Digital Twin — новая стратегическая позиция

### LDT IS

- strategic visual capability;
- personal representation;
- potential moat enhancer;
- progress visualization mechanism;
- long-term product direction;
- independently valid future validation track.

### LDT IS NOT

- universal gateway к ценности Ayla;
- обязательный элемент каждого journey;
- mandatory first-MVP critical path;
- единственный способ показать progress;
- центральная доменная сущность;
- prerequisite для recommendation/action/memory.

---

## 14. Relationship with MVP

Product Essence v1.2 должен установить только high-level boundary:

> Первый MVP не обязан реализовывать все strategic capabilities Essence.

Конкретное решение:

```text
Twin out of mandatory MVP
Food Intelligence in
Memory Foundation in
```

не копируется в Essence как вечный feature contract.

Эти решения принадлежат:

`Owner Decisions → MVP Scope → Roadmap`.

---

## 15. Relationship with Product Vision

После Essence v1.2 Product Vision должен сохранить distinction:

```text
Long-term strategy:
LDT remains important

MVP:
LDT is not mandatory

Product identity:
representation-neutral
```

Vision не должен снова превращать LDT в prerequisite пользовательской ценности.

---

## 16. Relationship with LDT Manifesto

LDT Manifesto остаётся canonical specialist owner для:

- recognition;
- identity continuity;
- classes of representation truth;
- correction;
- body dignity;
- LDT-specific consent;
- visual progression.

Essence не должен дублировать эти operational boundaries.

Позже отдельно проверить, не заявляет ли Manifesto власть над exact MVP scope.

---

## 17. Relationship with Product Thesis

После канонизации Essence v1.2 становится допустимым amendment Thesis:

```text
v0.5 LDT-dependent hypothesis
        ↓
v0.6 representation-neutral hypothesis
```

Целевая Thesis:

`Goal → Signal → Context → Recommendation → Action → Memory → Progress`.

---

## 18. Section mapping

| Product Essence v1.1 | Action | v1.2 target |
|---|---|---|
| §1 Ayla definition with LDT-primary framing | MODIFY | Representation-neutral identity + LDT strategic capability |
| §6 Killer Feature — LDT | MODIFY / RENAME | Strategic Visual Capability |
| §9 Primary visual interface = LDT | MODIFY | Conditional representation |
| §12 Main loop includes Twin | MAJOR MODIFY | Goal → Signal → Context → Recommendation → Action → Memory → Progress |
| §17 MVP value includes visual baseline | MAJOR MODIFY | Repeated value loop, release details downstream |
| §18 LDT boundary in MVP | RELOCATE | LDT Manifesto / future scope |
| §19 UX LDT-centric tests | MODIFY | Representation-neutral UX tests |
| §20 Transformation Goal center | KEEP | Central domain entity |
| §22 Decision tests | MODIFY | LDT conditional, not universal |

---

## 19. Non-goals

Этот amendment НЕ должен:

- удалять Living Digital Twin из Ayla;
- переписывать Product Essence целиком;
- добавлять Food Scanner как eternal Essence feature;
- проектировать Food Intelligence;
- проектировать Memory Foundation;
- менять business model;
- менять provider strategy;
- менять booking architecture;
- определять exact MVP Wave sequence;
- переписывать LDT Manifesto;
- менять safety/legal Constitution;
- добавлять unrelated product concepts.

---

## 20. Acceptance Criteria for Product Essence v1.2

Amendment считается успешным, если:

1. Transformation Goal остаётся центральной сущностью.
2. Human остаётся главным героем продукта.
3. LDT сохраняется как strategic capability.
4. LDT больше не является universal gateway к user value.
5. Основной product loop не требует создания Twin.
6. Первый MVP не обязан доказывать LDT viability.
7. Exact MVP composition передан MVP Scope.
8. Progress можно показать без LDT.
9. LDT-specific identity continuity остаётся обязательной только если LDT используется.
10. Food не становится новой вечной product identity.
11. Signal/context могут существовать независимо от конкретной capability.
12. Memory остаётся continuity mechanism.
13. Explainable recommendation и realistic action остаются частью пути.
14. Нет конфликта с OD-MVP-1…4.
15. Product Vision / Thesis / Principles могут быть обновлены downstream без нарушения Essence.
16. Не создаётся новая product hierarchy.
17. Незатронутые положения Essence остаются без stylistic rewrite.

---

## 21. Canonization sequence

После утверждения этого plan:

1. Author targeted `Product Essence v1.2`.
2. Провести consistency review только по затронутым разделам.
3. Product Owner approval.
4. Amend `Product Vision v2.0 → v2.1`.
5. Amend `Product Thesis v0.5 → v0.6`.
6. Amend `Product Principles v0.1 → v0.2`.
7. Update `MVP Scope and Release Contract`.
8. Update Roadmap / User Journey.
9. Вернуться к release backlog и MVP delivery.

### Scope-control

Не расширять этот amendment дополнительными улучшениями Essence без отдельного blocker.

---

## 22. Final Amendment Ruling

Правильное изменение Product Essence:

> **Ayla остаётся human-first transformation product с Transformation Goal в центре. Living Digital Twin сохраняется как стратегическая визуальная capability и потенциальный differentiator, но перестаёт быть обязательным шлюзом к пользовательской ценности и обязательным элементом первого MVP.**

Целевой фундаментальный цикл:

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

LDT может усиливать `State / Progress Representation`, но не обязан присутствовать в этом цикле.

**Next gate:** targeted authoring `Ayla Product Essence v1.2`, без открытия остальных частей Essence.
