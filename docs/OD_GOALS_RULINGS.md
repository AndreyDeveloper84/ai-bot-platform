# OWNER RULINGS — Progress Observations / Desired Outcome

**25.08.2026.** Записано дословно. Имеет приоритет над `PROPOSAL_PROGRESS_OBSERVATIONS.md` и над любым пересказом главного окна.

## GOALS-R1 — First-slice observation matrix

**УТВЕРЖДЕНО.**

Первый срез содержит только:

1. `WEIGHT`
   - numeric
   - unit = kg
   - origin = `user_stated`

2. `SELF_ASSESSMENT`
   - fixed ordinal scale
   - origin = `user_stated`

Других origin/types в первом срезе нет.

**Не входят:**
- measured/device;
- external;
- inferred;
- derived;
- symptom;
- события Food Diary как Outcome Progress evidence.

Любое расширение матрицы `observation_type × origin` требует **отдельного owner approval**.

**Persistent body observations остаются BLOCKED** до утверждённого consent/policy.

## GOALS-R2 — Self-assessment scale

Форму `меньше / без перемен / больше` **НЕ использовать** как значение observation: для первой точки нет базы сравнения, а последовательность relative deltas плохо образует временной ряд.

Утверждена фиксированная пользовательская ordinal-шкала:

```
0 — Не замечаю
1 — Немного заметна
2 — Заметна
3 — Сильно заметна
```

UX-вопрос для текущего примера: **«Насколько сейчас заметна отёчность?»**

Это **user-reported observation**, не медицинское измерение и не health inference.

Хранить **canonical value + canonical label**.

**LLM не должна сама преобразовывать свободный текст в новый уровень шкалы** без предусмотренного contract flow.

## GOALS-R3 — Horizon elapsed

Термин **УТВЕРЖДЁН**: `horizon_status = ELAPSED`, русский смысл — *истечение горизонта*.

Календарь:
- **не** ставит `ACHIEVED`;
- **не** ставит `FAILED`;
- **не** закрывает Desired Outcome;
- **не** обнуляет Progress;
- **не** удаляет observations.

**ВАЖНО: `elapsed` — не lifecycle status связи.** Разделить:

```
PlanOutcomeLink.status:
  - ACTIVE
  - CLOSED_BY_USER

PlanOutcomeLink.horizon_status:
  - NONE
  - UPCOMING
  - ELAPSED
```

После наступления даты допустимо: `status = ACTIVE`, `horizon_status = ELAPSED`.

Дальнейшее действие — **review через существующую Decision Policy**. Автоматического закрытия нет.

## GOALS-R4 — Horizon placement

**УТВЕРЖДЕНО:** горизонт живёт **на связи** `PersonalPlan ↔ DesiredOutcome`, а не внутри `DesiredOutcome` и не один на `PersonalPlan`.

Ввести в domain proposal сущность уровня **`PlanOutcomeLink`**.

**Основание:**
- **Desired Outcome** отвечает на вопрос «что человек хочет получить».
- **Personal Plan** — «как Ayla сопровождает сейчас».
- **PlanOutcomeLink** — «как данный outcome участвует в данном плане», включая индивидуальный горизонт.

Это позволяет:
- одному плану иметь N outcomes с разными сроками;
- сохранить историю старого плана;
- **продолжить тот же outcome в новом плане с другим горизонтом без клонирования/перезаписи результата.**

Несколько одновременно активных Desired Outcomes **разрешены с первого дня**.

Два одновременно активных Personal Plans для одного Outcome пока **НЕ вводить** без отдельной продуктовой причины.

## Дополнительное требование к evidence model

**Не смешивать две задачи:**

1. **Evidence Registry / compatibility matrix:** «может ли observation типа X использоваться для outcome типа Y?»
2. **Concrete provenance:** «какие конкретные observations были использованы для Progress конкретного outcome?»

Domain proposal должен показать **место** для concrete provenance/binding. Не обязательно реализовывать отдельную таблицу в первом срезе, **но схема не должна сделать такую связь невозможной**.

## Consent correction accepted

Уточнение принято: `ConsentType.HEALTH` имеет одного реального reader в `nutrition_context.py`, но Pilot holders = 0.

**Enum является техническим reuse candidate, но НЕ является legal authorization.**

До Registry amendment + Privacy/Legal approval **persistent body observations остаются BLOCKED**.

## Следующий шаг окна

**Миграцию НЕ создавать.**

Вернуть обновлённое предложение:

1. aggregate boundaries;
2. proposed entities + cardinality;
3. `PlanOutcomeLink`;
4. observation value model;
5. evidence registry vs concrete provenance;
6. lifecycle/horizon state machine;
7. consent enforcement point;
8. deletion/correction semantics;
9. API/document projection;
10. revised remaining SP estimate.

После этого — **review главного окна перед первой миграцией**.

---

## GOALS-R5 — Consent boundary: Desired Outcome vs Body Observation

**РЕШЕНИЕ ВЛАДЕЛЬЦА, 25.08.2026.** Противоречие GOALS-R3 / GOALS-R1 разрешено.

### 1. Desired Outcome НЕ является Progress Observation

Формулировка пользователя о желаемом результате:

- «хочу сбросить 10 кг»;
- «хочу уменьшить отёчность»;
- «хочу лучше спать»;

является `DesiredOutcome`.

Это **persistent user-stated intention / desired state**, а не наблюдение о фактическом состоянии тела.

```
DesiredOutcome != ProgressObservation != persistent body observation
```

### 2. Body Observation — фактическое состояние в момент времени

Примеры:
- «сейчас я вешу 96 кг» → `WEIGHT` ProgressObservation;
- «сейчас отёчность сильно заметна» → `SELF_ASSESSMENT` ProgressObservation.

Эти данные остаются **BLOCKED для persistent write** до утверждённого consent/policy для body/health observations.

### 3. Одно сообщение может породить два разных объекта

«Сейчас я вешу 96 кг и хочу сбросить 10 кг» разделяется на:

```
DesiredOutcome:
  desired_change = -10 kg

ProgressObservation:
  WEIGHT = 96 kg
  origin = user_stated
```

**Если observation consent отсутствует, отказ записи Observation НЕ должен уничтожать допустимую запись Desired Outcome.**

### 4. Desired Outcome тоже требует policy gate

GOALS-R3 остаётся в силе: persistent Desired Outcome **нельзя** писать без утверждённого consent/policy.

Но **HEALTH scope не применяется к Desired Outcome автоматически только из-за телесной тематики.**

Для Desired Outcome должна существовать **отдельная именованная consent/policy integration point**. Какой существующий Consent Scope её питает — проверить по Registry отдельно. До этой проверки gate может оставаться **fail-closed**.

### 5. Что блокирует Privacy/Legal

Privacy/Legal blocker относится к **включению persistent body observations**:
- `WEIGHT`;
- `SELF_ASSESSMENT`;
- будущие measured body observations.

Он **НЕ блокирует проектирование**:
- `PersonalPlan`;
- `DesiredOutcome`;
- `PlanOutcomeLink`;
- Observation schema;
- Evidence Registry;
- provenance;
- deletion/correction;
- API/document contracts;
- fail-closed enforcement.

### 6. Первая миграция

После утверждения domain proposal **миграцию архитектурного каркаса создавать можно**.

Но **write-path body observations должен оставаться технически закрытым** до Registry amendment + Privacy/Legal approval.

**Никаких временных bypass / feature-flag разрешений.**

### 7. Пример «отёчность»

```
«Хочу уменьшить отёчность»    → DesiredOutcome
«Сейчас сильно заметна»       → SELF_ASSESSMENT ProgressObservation
«Отёчность вызвана натрием»   → inference; запрещено, система его не создаёт
```

## Следствие для V2 — окну разрешено идти дальше

Окно идёт к **финальному domain proposal**, затем на **review перед миграцией**.

Не надо ждать юристов, чтобы решить cardinality, `PlanOutcomeLink`, supersede, provenance и вычисляемый `horizon_status`.

**Privacy/Legal ставится отдельным release gate:**

```
Schema ready
   ↓
code deployed fail-closed
   ↓
Privacy/Legal + Registry
   ↓
consent integration verified
   ↓
body observation writes enabled
```

> Так мы не блокируем инженерную работу, но и не превращаем «схема уже готова» в основание начать собирать чувствительные данные.

---

# REVIEW FINAL / OWNER RULING — 25.08.2026

**Финальное предложение V3 ПРИНЯТО концептуально.** Разрешение на первую миграцию каркаса даётся **после внесения amendments ниже**.

## GOALS-R6 — persistent Desired Outcome consent scope

Для persistent `explicit_goal` создать **отдельный Consent Scope: `goal_memory`**.

**Не расширять `preference_memory`.**

**Назначение:** persistent хранение только явно сформулированных пользователем goals / Desired Outcomes между сессиями.

**Allowed data:** `explicit_goal`.
**Provenance:** `user_stated` only.

**Не входят:** body observations, symptom, inferred/derived goals, behavioral inference.

**Одна именованная policy point: `goal_intention_gate`.**

Эта же точка должна стать policy boundary существующего persistent `ClientGoal`. Текущая запись `ClientGoal` без зарегистрированного persistent scope считается **governance debt, а не прецедентом**.

**Не выполнять автоматический consent backfill существующих данных.**

## AMENDMENT A — Outcome Progress baseline

Baseline берётся **не раньше `DesiredOutcome.created_at`**.

История человека до появления Outcome **не становится Progress этого Outcome**.

Новый Personal Plan для того же Outcome **baseline не сбрасывает**.

## AMENDMENT B — self-assessment instrument

`SELF_ASSESSMENT` должен нести **versioned `instrument/scale_code`**.

Первый instrument: **`NOTICEABILITY_0_3_V1`** с утверждёнными canonical labels.

Evidence Registry включает **instrument** в допустимость.

## AMENDMENT C — consent withdrawal

Gate O защищает **product processing**.

Он **НЕ должен блокировать**:
- inspect / export;
- correction;
- deletion;
- выполнение data-subject rights

для уже сохранённых данных **после revoke**.

После revoke observations **не используются** для Progress / Recommendation до нового разрешения.

## AMENDMENT D — idempotency

Composite intent endpoint обязан иметь **idempotency на уровне запроса и независимых suboperations**: `outcome`, `observation`.

**Retry после partial success не создаёт второй `DesiredOutcome`.**

## AMENDMENT E — consent attestation

Domain service **не принимает `consent=True/False`**.

Контракт предусматривает **typed `ConsentAttestation` с authority/provenance**.

Конкретная topology подключения определяется после Registry amendment. **Fail-closed сохраняется.**

## GO на следующий шаг

После внесения этих пяти уточнений **разрешена первая миграция каркаса `wellness`**.

**Разрешение на миграцию НЕ является разрешением включить persistent writes.**

- Gate D активируется **только после утверждения `goal_memory`**;
- Gate O остаётся закрытым до Registry + Privacy/Legal + verified consent integration.

**После миграции — readback схемы и review перед включением любого writer.**
