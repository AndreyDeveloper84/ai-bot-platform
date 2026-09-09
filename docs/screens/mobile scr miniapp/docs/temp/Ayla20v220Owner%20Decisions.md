# Ayla MVP v2 — Owner Decisions

**Status:** Owner Approved  
**Purpose:** Зафиксировать четыре продуктовых решения, определяющих ревизию `Ayla MVP Scope and Release Contract v0.3` и последующее обновление MVP Roadmap.  
**Decision authority:** Product Owner  
**Scope:** MVP release boundary

---

## Контекст

Текущий `Ayla MVP Scope and Release Contract v0.3` был сформирован вокруг MVP-гипотезы, в которой Living Digital Twin является обязательной частью critical path.

После пересмотра Product Principles принято изменить первый MVP так, чтобы он проверял не отдельную модель цифрового двойника, а способность Ayla создавать повторяемую пользовательскую ценность через понимание цели, повседневного контекста, рекомендации, действия, память и прогресс.

Этот документ фиксирует owner decisions. Он не заменяет существующий MVP Scope. Следующим шагом решения должны быть проведены через соответствующие upstream/downstream документы.

---

# OD-MVP-1 — Living Digital Twin

**Decision: ACCEPTED**

Living Digital Twin исключается из обязательного critical path первого MVP.

LDT не является обязательным условием запуска MVP, controlled pilot, доказательства основной MVP-гипотезы или прохождения MVP release gate.

Из MVP release dependencies должны быть удалены требования, существующие исключительно ради обязательного LDT, включая:

- обязательный Twin baseline;
- recognition/correction Twin как release gate;
- обязательную фотофиксацию тела;
- LDT media pipeline как обязательную release dependency;
- recognition quality bar как блокер выпуска;
- LDT-specific release evidence, если оно не используется другой MVP-capability.

### Что это решение не означает

Living Digital Twin не признаётся ошибочной концепцией и не удаляется из долгосрочного видения Ayla.

Он переводится в **Post-MVP / Later Validation** и может быть возвращён в roadmap после появления доказательств, что существенно улучшает user outcome.

### Критерий возвращения

> LDT должен доказуемо улучшать способность Ayla понимать пользователя, давать более полезные рекомендации или показывать значимый прогресс.

---

# OD-MVP-2 — Food Intelligence

**Decision: ACCEPTED**

Food Intelligence / Food Scanner включается в обязательный MVP как первый реализованный источник повседневного пользовательского контекста.

Food Scanner не является самостоятельным calorie-tracking продуктом.

Его роль:

```text
Food input / photo
        ↓
Recognition
        ↓
User correction when needed
        ↓
Useful observation
        ↓
Goal context
        ↓
Explainable recommendation
        ↓
Action / memory candidate
```

### Минимальный MVP scope

- пользователь может передать информацию о еде, включая фото;
- Ayla может извлечь полезное наблюдение;
- пользователь может исправить существенную ошибку распознавания;
- наблюдение может быть связано с Transformation Goal;
- Ayla может использовать его при формировании безопасной рекомендации;
- допустимая информация может стать memory candidate.

### Не входит автоматически

- полноценный дневник питания;
- точный calorie tracking;
- обязательный подсчёт БЖУ;
- диетические программы;
- медицинские выводы;
- diagnosis/inference из пищевых сигналов;
- social/gamification-механики питания.

### Safety invariant

> Food observation ≠ medical fact.

---

# OD-MVP-3 — Memory Foundation

**Decision: ACCEPTED**

Memory Foundation становится частью MVP с самого начала.

При этом MVP не обязан до первого пилота реализовывать полноценную long-term personalization platform.

```text
Current interaction
        ↓
Working context
        ↓
Memory candidate
        ↓
Policy / consent gate
        ↓
Persistent memory
```

### Обязательная foundation

Архитектура MVP должна предусматривать:

- working/session context;
- различие observation и confirmed fact;
- memory candidates;
- policy/consent boundary;
- возможность последующего persistent storage;
- provenance/источник существенных фактов там, где это требуется;
- возможность объяснить, какой контекст повлиял на рекомендацию.

### Persistent memory

Cross-session persistent memory вводится постепенно и только там, где выполнены необходимые consent/privacy условия.

Пользователь должен иметь возможность видеть, исправлять, удалять и ограничивать/отключать persistent memory.

### Hard invariants

> Observation ≠ Persistent Memory Fact.

> AI inference ≠ User Fact.

---

# OD-MVP-4 — MVP Hypothesis

**Decision: ACCEPTED**

Основная гипотеза первого MVP изменяется.

MVP больше не обязан доказывать ценность Living Digital Twin.

Первый MVP должен доказать, что Ayla способна создавать полезный повторяемый цикл:

```text
Goal
  ↓
Signal
  ↓
Context
  ↓
Recommendation
  ↓
Action
  ↓
Memory
  ↓
Progress
  ↺
```

Где:

- **Goal** — цель определяется пользователем;
- **Signal** — релевантный повседневный сигнал; Food Intelligence является первым реализованным сигналом;
- **Context** — Ayla понимает сигнал в контексте цели и доступной информации;
- **Recommendation** — Ayla предлагает объяснимый следующий шаг;
- **Action** — пользователь может выполнить реалистичное действие; booking является одним из вариантов, но не целью продукта;
- **Memory** — полезный контекст может продолжить работать в следующих взаимодействиях в рамках consent/privacy;
- **Progress** — Ayla помогает пользователю понимать движение относительно его собственной цели.

### MVP validation question

> Может ли Ayla понять цель пользователя, использовать релевантный повседневный контекст, предложить полезный и объяснимый следующий шаг, помочь выполнить действие, сохранить допустимую непрерывность и со временем показать пользователю значимый прогресс?

### Не являются самостоятельным доказательством успеха

- количество сообщений;
- DAU само по себе;
- количество booking;
- время в приложении;
- количество распознанных фотографий еды;
- количество записанных memory facts.

Это могут быть диагностические метрики, но они не заменяют user outcome.

---

# Совместный эффект решений

### Было

```text
Goal
  ↓
Living Digital Twin
  ↓
Recommendation
  ↓
Action / Booking
  ↓
Progress
```

### Становится

```text
Goal
  ↓
Everyday Signal
(Food first)
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
```

Living Digital Twin перестаёт быть обязательной промежуточной сущностью между пользователем и ценностью Ayla.

---

# Документные последствия

Рекомендуемый порядок синхронизации:

1. `Ayla Product Thesis v0.5` — проверить нормативную зависимость MVP hypothesis от Living Digital Twin.
2. `Ayla MVP Scope and Release Contract v0.3` — провести OD-MVP-1…4 через release boundary.
3. `Ayla MVP Roadmap` — обновить Wave 0–3 без перепроектирования всей release-модели.
4. `Ayla User Journey` — заменить обязательный Twin path новым MVP loop.
5. Downstream scopes / migration documents — синхронизировать после фиксации upstream.

### Change-control rule

Не следует массово переписывать downstream-документы до обновления их upstream source of truth.

---

# Owner Decision Register

| ID | Решение | Статус |
|---|---|---|
| OD-MVP-1 | Living Digital Twin исключается из обязательного MVP critical path и переносится в Post-MVP / Later Validation | **ACCEPTED** |
| OD-MVP-2 | Food Intelligence / Food Scanner входит в обязательный MVP как первый everyday contextual signal | **ACCEPTED** |
| OD-MVP-3 | Memory Foundation входит в MVP с самого начала; persistent memory вводится постепенно через policy/consent gates | **ACCEPTED** |
| OD-MVP-4 | MVP проверяет цикл `Goal → Signal → Context → Recommendation → Action → Memory → Progress`, а не обязательную ценность Twin | **ACCEPTED** |

---

## Итоговое owner ruling

> Первый MVP Ayla должен доказать не технологическую полноту продукта и не ценность отдельной сущности Living Digital Twin, а способность Ayla создавать повторяемую персональную ценность: понимать цель пользователя, использовать релевантный повседневный контекст, объяснимо рекомендовать следующий шаг, помогать действовать, сохранять разрешённую непрерывность и показывать прогресс относительно цели пользователя.

**Owner decisions OD-MVP-1…4 зафиксированы и приняты.**
