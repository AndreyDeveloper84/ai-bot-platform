# Ayla DecisionReadiness Engine — Specification v1.0

**Статус:** DRAFT → IMPLEMENTATION-READY по всему, кроме содержимого таблицы
безопасности (её у владельца нет, см. §16.2) и трёх порогов (§16.1).
**Трек:** A. Граница трека — **готовность решения и следующий вопрос**.
**Канон:** `docs/ayla-conversation-state-v1.1-reconciled.md` (Working Canon, 14 решений),
Decision 5 (§8) — основной; Decision 4 (§7), Decision 6 (§9), Decision 10 (§13),
Decision 11 (§14), Decision 13 (§16) — потребляемые.
**Переиспользует:** DRF-1533 (`separation` / `completeness` / `conflicts`),
DRF-1519 (пороги остановки), DRF-1531 (различающий вопрос словами каталога),
DRF-1529 (эпик, очерёдность), `apps/orchestrator/refusal_memo.py`
(реестр «уже сказано»), `apps/orchestrator/discovery.py` (три режима вопроса).
**Дата:** 2026-09-07.
**Код не менять.** Документ read-only относительно репозиториев.

---

## 0. Что этот документ решает и чего не решает

DecisionReadiness — **детерминированная оценка того, достаточно ли подтверждённых
свидетельств, чтобы принять следующее решение о рекомендации** (канон §8).

Документ задаёт:

* контракт входа и выхода механизма;
* владение каждым шагом;
* обязательный и условно-обязательный контекст;
* блокировки;
* влияние делегирования;
* стык с безопасностью и с набором кандидатов;
* семантику следующего вопроса, стабильный `question_id`, учёт заданного/отвеченного;
* реестр `reason_codes`, fail-closed, идемпотентность, версионирование, аудит.

**Non-goals (принадлежат другим трекам):**

| Не здесь | Где |
|---|---|
| Модель Goal, её жизненный цикл | трек B |
| Формула и стадии ранжирования, резолверы | трек C |
| Plan, его версии и валидация | трек D |
| Матрица сигналов безопасности и продуктовые формулировки | у владельца, канон §7 «Still open» |
| Тексты вопросов и кнопок | соседняя работа (DRF-1519 «не менять тексты») |

Взаимодействие с безопасностью описано; **политика безопасности не проектируется**.

---

## 1. Read-only discovery: что уже есть на самом деле

Всё в этом разделе проверено чтением кода 07.09.2026. Ветка рабочего дерева —
`docs/ux-canon-reconciliation` (отстаёт от `dev`), поэтому часть фактов снята
через `git show origin/dev:…` и это отмечено.

### 1.1 Три типа вопроса — есть, но названы иначе, чем в брифе

`apps/orchestrator/discovery.py`, `origin/dev` строки **440–442**
(в брифе указано 430–432 — блок тот же, номера сместились; в рабочей ветке 249–251):

```python
CLARIFICATION_MODE_CONFIRM_ONE = "confirm_one"
CLARIFICATION_MODE_CHOOSE_MANY = "choose_many"
CLARIFICATION_MODE_FREE = "free"
```

**Точность имени важна:** третья константа называется `CLARIFICATION_MODE_FREE`
со значением `"free"`, а не `FREE_CLARIFICATION`. Спецификация стыкуется
со значениями `"confirm_one" / "choose_many" / "free"` — четвёртого механизма
не вводится.

Сопутствующее, проверено:

* `normalize_clarification_mode()` (`discovery.py:~1102`): при отсутствии явного
  режима выводит `confirm_one` при наличии опций и `free` без них;
  `choose_many` **никогда не выводится**, только объявляется явно.
* `clarification_mode_of(action_data)` — обратное чтение режима потребителем.
* `ASK_CLARIFICATION_TOOL_SPEC` — вопрос задаётся **как вызов инструмента**,
  плоский зеркальный аналог `ayla_ai_core.tools.ASK_CLARIFICATION`
  (`ayla-ai-core/src/ayla_ai_core/tools.py:162`, `TOOL_DEFINITIONS[4]`).

### 1.2 Реестр «уже сказано» — существует ровно один, и он про отказы каталога

`apps/orchestrator/refusal_memo.py` (**только на `origin/dev`**, в рабочей ветке
файла нет). Форма, которую спецификация переиспользует буквально:

* хранилище — `Conversation.skill_state` (`apps/conversations/models.py:161`,
  `JSONField(default=dict)`), **непересекающийся ключ** `STATE_KEY = "no_match"`;
* TTL `STATE_TTL_SECONDS = 1800`;
* ограниченный список `_MAX_ENTRIES = 5`, повтор того же ключа **обновляет,
  а не добавляет**;
* контракт записи — best-effort: «never break a turn over a hint».

Это ровно та конструкция, которой не хватило DRF-1542. Спецификация вводит
второй непересекающийся ключ в том же поле — и **меняет контракт записи
с best-effort на fail-closed** (§13.4), потому что реестр вопросов не подсказка,
а условие корректности.

### 1.3 Уверенность модели сегодня действительно решает

`apps/orchestrator/pipeline.py:180–235`, `_confidence_floor_reason()`:
`SkillResult.confidence` (число, которое кладёт навык) сравнивается
с `SKILL_CONFIDENCE_HANDOFF_THRESHOLD` / `AI_CONFIDENCE_HANDOFF_THRESHOLD`
и при недоборе поднимает эскалацию к человеку. Это не тот же контур, что
рекомендация, но это живой пример «число от модели меняет поведение диалога» —
то, что канон §8 запрещает для DecisionReadiness.

Отдельно и в пользу проекта: `MemoryEntry` **запрещает** поле `confidence`
на уровне теста (`apps/identity/tests/test_memory_entry_step3b_provenance.py:105`,
`test_no_confidence_field`). Дисциплина уже существует в памяти — здесь она
распространяется на готовность решения.

### 1.4 Свидетельство модели сегодня подменяет свидетельство человека

`apps/orchestrator/nutrition_global.py:262–286` — проверено дословно:

```python
arg_key = {"health_screening": "symptom_text", ...}[name]
text = str(args.get(arg_key) or "").strip()
...
context = _build_context(message_text=text, ...)
if not skill.matches(context):
    return None
```

`args` — аргументы **вызова инструмента, сочинённые моделью**. Они подставляются
в `message_text` — поле, которое по имени и по всем остальным вызовам означает
реплику человека. Вето навыка проверяет пересказ модели против классификатора
модели. Это и есть нарушение EVIDENCE ORIGIN в чистом виде, и это механизм
DRF-1542 (пять одинаковых экранов).

**По DRF-1542 работает чужой исполнитель. Код не трогается.** Спецификация
формулирует барьер, а не правку; барьер совпадает с пунктом B самой DRF-1542
(«вето считать по реплике человека»), поэтому конфликта нет.

### 1.5 Величины DRF-1533 — где они вычисляются

DRF-1533 ссылается на `_parse_query()` и `_matched_services()`. Фактический
адрес — **`apps/marketplace/discovery.py:693` и `:1062`**, не
`apps/orchestrator/discovery.py`. `_matched_services()` уже реализует
маленький частный случай `separation`: услуга проставляется на карточку,
только если совпавшие услуги мастера **схлопываются в ровно одну**;
несколько равно совпавших → мастер намеренно отсутствует в результате,
чтобы не пронести в запись услугу, которой человек не выбирал.

Замер владельца (`OPEN_DECISIONS.md` §41, 07.09) и сама DRF-1533 говорят одно:
сегодня `separation` **тождественно ноль**, а единственная причина рекомендации
на проде — `reasoning_text = «Рейтинг 4.9»` при `reviews_count: 0`.

### 1.6 Чего нет вообще (проверено поиском по обоим репозиториям)

| Сущность | Поиск | Результат |
|---|---|---|
| `DecisionReadiness` / `decision_readiness` | `apps/**`, `ayla-ai-core/**` | не найдено |
| `state_revision` (канон §13.4) | там же | не найдено |
| `decision_id` (канон §13.1) | там же | не найдено |
| `delegation` как состояние | `apps/**` | одно совпадение в `apps/nutrition_proactive/tests/test_tasks.py` — не то |
| `SemanticOption` / `StructuredDecision` | `ayla-ai-core/src/**` | не найдено |
| `SemanticConversationState` | `ayla-ai-core/src/**` | не найдено |

`ayla-ai-core/src/ayla_ai_core/` содержит `composer.py`, `context.py`, `memory.py`,
`observability.py`, `orchestrator.py`, `prompts.py`, `tools.py`, `tool_handlers.py`,
`providers/`. Семантических контрактов канона §6 там **нет ни одного**.

---

## 2. Gap analysis

| № | Пробел | Доказательство | Следствие для спецификации |
|---|---|---|---|
| G1 | Нет монотонного `state_revision` | §1.6 | Введён как **предусловие** (§4.1). Без него защита от устаревшего колбэка и ключ идемпотентности неисполнимы |
| G2 | Нет типа «свидетельство» с происхождением | §1.6, §1.4 | Введён `ConfirmedEvidence` / `ModelSignal` как **разные типы** (§3) |
| G3 | Нет реестра заданных вопросов | §1.2 — реестр только про отказы каталога | Введён `question_ledger` по образцу `refusal_memo` (§13.4) |
| G4 | Нет стабильного `question_id` | §1.6 | Введён детерминированный вывод (§13.2) |
| G5 | Нет `delegation` как состояния | §1.6 | Введено как контролируемое поле, поднимаемое только явным `DELEGATE` (§10) |
| G6 | `separation` сегодня ≡ 0 | DRF-1533, §41 | Введена **обязательная очерёдность внедрения** (§18) |
| G7 | Нет матрицы сигналов безопасности | канон §7 «Still open», OD §40.4 п.3 | Таблица объявлена, **содержимое UNKNOWN**, поведение при пустой таблице задано (§11.4) |
| G8 | `recommendation_eligible` не отделён от `catalog_visible` в рантайме | канон §14, OD §40.2 п.5, §40.3(д) | Введён как обязательный вход, отсутствие → fail-closed (§12.3) |
| G9 | Число от модели решает поведение | §1.3 | Запрет с проверяемой формулировкой (§7, §17.2) |

---

## 3. Модель понятий

### 3.1 Происхождение свидетельства — типовое разделение, не поле

Это **структурный** ответ на инвариант EVIDENCE ORIGIN. Разделение сделано
типами, а не значением поля, потому что поле можно заполнить неверно, а тип —
нет: у `ConfirmedEvidence` **не существует конструктора из вывода модели**.

```
EvidenceOrigin  (закрытое перечисление)
├── USER_TEXT          явный текст человека
├── USER_ACTION        явный клик: (decision_id, option_id)
├── DOMAIN_AUTHORITY   авторитетный доменный факт (бэкенд Ayla)
├── PROMOTED_MEMORY    подтверждённая память/цель, прошедшая promotion (канон §11)
└── MODEL_INFERENCE    вывод модели

CONFIRMABLE_ORIGINS = {USER_TEXT, USER_ACTION, DOMAIN_AUTHORITY, PROMOTED_MEMORY}
```

```
ConfirmedEvidence                       ModelSignal
├── evidence_id                         ├── signal_id
├── slot                                ├── slot
├── value                               ├── value
├── origin ∈ CONFIRMABLE_ORIGINS        ├── origin = MODEL_INFERENCE (константа типа)
├── source_ref                          ├── produced_at_revision
│    USER_TEXT      → message_id        └── extractor_id
│    USER_ACTION    → decision_id+option_id
│    DOMAIN_AUTHORITY → (api, entity_id, fetched_at)
│    PROMOTED_MEMORY → memory_entry_id
├── observed_at
└── captured_at_revision
```

**Правила, делающие подмену невозможной структурно:**

1. `ConfirmedEvidence` строится **только** четырьмя функциями приёма, по одной
   на происхождение. Каждая требует `source_ref` того типа, который может
   породить **только** её канал. Функции приёма `MODEL_INFERENCE` среди них нет.
2. Конверсии `ModelSignal → ConfirmedEvidence` не существует. Единственный
   переход — `confirm(signal, confirming_event)`, где `confirming_event` —
   `SemanticUserEvent` с `revision > signal.produced_at_revision`;
   результат получает `origin = USER_TEXT | USER_ACTION` и `source_ref`
   **подтверждающего события**, а не сигнала. Сигнал остаётся в аудите
   как `confirmed_by`.
3. Вход движка (§4) принимает `ConfirmedEvidence[]` и `ModelSignal[]`
   **разными полями**. Функция удовлетворения обязательного контекста
   (§8.3) читает только первое.
4. `ModelSignal` допустим ровно для одного: **выбрать, о каком слоте спросить**.
   Он никогда не закрывает слот, не снимает блокировку, не поднимает
   `delegation` и не разрешает повтор вопроса.

**Проверка инварианта (обязательный тест):** подать вход, где `ModelSignal`
покрывает все обязательные слоты, а `ConfirmedEvidence[]` пуст. Ожидание —
`NEEDS_REQUIRED_CONTEXT`, `allow_recommend = false`,
`reason_codes ∋ EVID_MODEL_SIGNAL_NOT_EVIDENCE`. Тест обязан краснеть
при удалении фильтра по происхождению.

### 3.2 Слот

Слот — именованная семантическая ячейка контекста (`body_area`, `city`,
`service`, `budget`, `time_window`, …). Слоты объявлены в `RequiredContextSpec`
(§8). Значение слота имеет три различимых состояния (канон §16.2 —
`null` не должен схлопывать `UNKNOWN` и `FLEXIBLE`):

```
KNOWN(value)   есть подтверждённое свидетельство
UNKNOWN        не спрашивали или не ответили
FLEXIBLE       человек явно сказал «неважно / любой»
```

`FLEXIBLE` — **удовлетворяет** обязательный контекст и **запрещает** вопрос
по этому слоту. `UNKNOWN` — не удовлетворяет. Их схлопывание — дефект.

---

## 4. Input contract

```
ReadinessInput
├── spec_version              str      контракт этого документа
├── policy_version            int      версия порогов и RequiredContextSpec
├── state_revision            int      монотонный, из ConversationState (предусловие G1)
├── surface                   enum     MAX_CHAT | MINIAPP | …   (только для аудита)
├── mode                      enum     DISCOVERY | EXECUTION    (канон §3)
├── current_need              CurrentNeed | None
├── evidence                  ConfirmedEvidence[]
├── model_signals             ModelSignal[]
├── delegation                LOW | MEDIUM | HIGH
├── safety                    SafetyResult
│    ├── state                NORMAL | CLARIFY | CAUTION | STOP | UNKNOWN
│    ├── rule_id              str | None
│    ├── policy_version       str | None
│    ├── required_slots[]     слоты, которых требует безопасность
│    └── forbidden_capabilities[]
├── candidates                CandidateSetSignature      (§12)
├── required_context_spec     RequiredContextSpec        (§8)
├── question_ledger           QuestionLedger             (§13.4)
└── availability              InputAvailability
     ├── ledger_readable      bool
     ├── probe_available      bool
     ├── candidates_fresh     bool
     └── safety_evaluated_at_revision int
```

### 4.1 Предусловия входа (нормативно)

* **P1.** `state_revision` монотонно возрастает в пределах сессии. Сегодня
  отсутствует (G1) — движок нельзя включать до его появления.
* **P2.** Просроченные свидетельства **отфильтрованы адаптером входа**,
  а не движком. Внутри движка часов нет (§17.1).
* **P3.** `safety.state` вычислен для **этого же** `state_revision`.
  Если `safety_evaluated_at_revision < state_revision` — вход несвежий,
  см. §15.2.
* **P4.** `candidates` вычислены после применения **жёстких** фильтров
  (город, активность, способность исполнителя) — канон §9.1.
* **P5.** Никакое поле входа не построено из текста, сочинённого моделью,
  кроме `model_signals`. Нарушение P5 — дефект вызывающего, а не движка;
  проверяется тестом §3.1.

---

## 5. Output contract

```
ReadinessOutput
├── readiness_state    READY | NEEDS_DISCRIMINATION | NEEDS_REQUIRED_CONTEXT
│                      | INSUFFICIENT_EVIDENCE | BLOCKED
├── allow_recommend    bool                (§10 — вычисляется ПОСЛЕ состояния)
├── blockers[]         Blocker             (непусто ⟺ state == BLOCKED)
│    ├── blocker_type  SAFETY_STOP | SAFETY_UNKNOWN | NO_ADMISSIBLE_CANDIDATES
│    │                 | CATALOG_NOT_RECOMMENDABLE | READINESS_INPUT_UNAVAILABLE
│    │                 | ASK_BUDGET_EXHAUSTED
│    └── attribution   rule_id / policy_version / slot / digest
├── next_question      NextBestQuestion | None            (§13)
├── measures           { separation, completeness, conflicts }   (§9)
├── required_context   { slot: SlotVerdict }              (§8.3)
├── reason_codes[]     закрытый реестр, канонически отсортирован  (§16)
├── evaluation         DecisionEvidence                   (§19)
└── readiness_key      str                                (§17.3)
```

**Инвариант формы выхода (проверяемый):**

```
state == BLOCKED           ⟺ blockers ≠ ∅
state == READY             ⟹ next_question is None
state ∈ {NEEDS_DISCRIMINATION, NEEDS_REQUIRED_CONTEXT, INSUFFICIENT_EVIDENCE}
                           ⟹ next_question is not None
                              OR blockers ∋ ASK_BUDGET_EXHAUSTED  (тогда state = BLOCKED)
allow_recommend            ⟹ state ∈ {READY, NEEDS_DISCRIMINATION}
```

Последняя строка — половина инварианта DELEGATION CEILING (§10.2).

Числовых оценок готовности на выходе **нет**. `separation` — служебная
величина для аудита и порога (канон §8: «numerical internal ranking scores
may exist, but are not probability/confidence and are not a public semantic API»),
она **не** отдаётся на поверхность и не переводится в текст для человека.

---

## 6. Ownership

| Шаг | Владелец | Классификация |
|---|---|---|
| Нормализация MAX-ввода в `SemanticUserEvent` | `ai-bot-platform` | DETERMINISTIC |
| Извлечение семантических сигналов из языка | LLM | **LLM_ALLOWED** — только `ModelSignal` |
| Присвоение `origin` свидетельству | адаптер приёма, `ai-bot-platform` | DETERMINISTIC |
| Подтверждение `ModelSignal → ConfirmedEvidence` | `ai-bot-platform` | DETERMINISTIC |
| Жёсткая фильтрация кандидатов, наличие, цена, способность | бэкенд Ayla | AUTHORITATIVE_DOMAIN |
| `recommendation_eligible` (маппинг VERIFIED) | бэкенд Ayla | AUTHORITATIVE_DOMAIN |
| Вычисление `separation` / `completeness` / `conflicts` | резолвер (DRF-1533) | DETERMINISTIC |
| Извлечение сигналов безопасности | LLM + лексические детекторы | **LLM_ALLOWED** |
| Решение политики безопасности | Safety Engine (`ayla-ai-core`) + правила (`ayla-knowledge`) | **LLM_FORBIDDEN**, CONTROLLED_POLICY |
| **Вычисление `readiness_state`** | `ayla-ai-core` | **DETERMINISTIC, LLM_FORBIDDEN** |
| Пороги τ, N, потолки вопросов | конфигурация | CONTROLLED_POLICY |
| Выбор следующего вопроса (`question_id`) | `ayla-ai-core` | **DETERMINISTIC, LLM_FORBIDDEN** |
| Формулировка текста вопроса | LLM | **LLM_ALLOWED** — рендер утверждённой семантики |
| Добавление новой опции, которой нет в `SemanticOption[]` | — | **LLM_FORBIDDEN** (канон §13.1) |
| Разрешение повтора вопроса | реестр + правило §13.5 | **DETERMINISTIC, LLM_FORBIDDEN** |
| Оркестрация | `ai-bot-platform` | DETERMINISTIC |
| Персистенция `DecisionEvidence` | бэкенд Ayla | AUTHORITATIVE_DOMAIN |

**Обоснование каждого `LLM_ALLOWED`:** во всех трёх случаях модель производит
только *кандидатное* значение, которое затем либо подтверждается человеком
(извлечение сигналов), либо ограничено заранее выданным закрытым множеством
(рендер вопроса и опций). Ни в одном из них вывод модели не становится
основанием решения.

---

## 7. Категорические запреты (нормативно, проверяемо)

| № | Запрет | Как проверяется |
|---|---|---|
| Z1 | LLM confidence score в любом виде на входе или внутри движка | В `ReadinessInput` нет ни одного числового поля, происходящего от модели. Тест: рефлексия по полям — ни одно не помечено `MODEL_INFERENCE`-происхождением, кроме `model_signals` |
| Z2 | «Модель считает, что информации достаточно» | `evaluate()` не делает вызовов LLM. Тест: подменить провайдера на падающий стенд — `evaluate()` отрабатывает |
| Z3 | Произвольный следующий вопрос | `next_question.question_id` обязан присутствовать в `QuestionCatalog` под текущим `policy_version`. Вопрос вне каталога → `BLOCKED(READINESS_INPUT_UNAVAILABLE)` |
| Z4 | Повтор семантического вопроса без controlled reason | §13.5, реестр §13.4 |
| Z5 | Продуктовое решение внутри промпта | Пять состояний вычисляются функцией `evaluate()`. Тест: промпт не содержит ни одного имени состояния (`grep` по `prompts.py` / `concierge.py`) — состояния не сообщаются модели как её задача |

**Про Z5 отдельно.** Пять состояний реализуемы без скрытого продуктового
решения в промпте потому, что каждое из них выводится из **наблюдаемых
величин**, а не из формулировки: состояние безопасности (перечисление из
Safety Engine), множество неудовлетворённых обязательных слотов (сравнение
множеств), мощность допустимого множества кандидатов (число), `separation`
(число из резолвера), флаг доступности входа (булево). Модель ни на одну
из них не влияет.

---

## 8. Required evidence и conditional required evidence

### 8.1 `RequiredContextSpec`

Таблица, загружаемая по `policy_version`. **Данные, а не код.**

```
RequiredSlot
├── slot                 str
├── owner                SAFETY | EXECUTION | CATALOG | RANKING
├── required_when        предикат (§8.2) — DETERMINISTIC, без LLM
├── satisfied_by_origins подмножество CONFIRMABLE_ORIGINS
├── flexible_allowed     bool     — можно ли закрыть слот ответом «неважно»
├── volatility           STABLE | VOLATILE(ttl_seconds)
└── question_id          ссылка в QuestionCatalog
```

`owner = SAFETY` — единственная группа, которую **не может** закрыть
`delegation=HIGH` и которая при неудовлетворённости всегда даёт
`NEEDS_REQUIRED_CONTEXT`, а не `NEEDS_DISCRIMINATION`.

### 8.2 Условная обязательность — язык предикатов

`required_when` — выражение из **закрытого** набора термов, чтобы предикат
можно было и вычислить, и показать в аудите:

```
candidate_set.spans_more_than_one(<field>)
candidate_set.cardinality > N
slot(<s>).state == UNKNOWN | KNOWN | FLEXIBLE
mode == DISCOVERY | EXECUTION
safety.state == NORMAL | CLARIFY | CAUTION | STOP
execution.requires(<param>)
catalog.recommendation_eligible_count == 0
∧  ∨  ¬
```

Обоснованные примеры (каждый опирается на существующий факт, не выдуман):

| Слот | `required_when` | Основание |
|---|---|---|
| `city` | `candidate_set.spans_more_than_one(city) ∧ slot(city).state == UNKNOWN` | канон §9.1 «город — фильтр»; OD §41 п.4: выдача сегодня межтенантная без гео-ограничения |
| `service` | `mode == EXECUTION ∧ execution.requires(ayla_service_id) ∧ slot(service).state == UNKNOWN` | `_matched_services()` (`apps/marketplace/discovery.py:1062`) отдаёт услугу только при однозначности; иначе воронка требует спросить |
| `budget` | `slot(budget).state == UNKNOWN ∧ candidate_set.spans_more_than_one(price_band)` — **только `owner=RANKING`**, то есть NEEDS_DISCRIMINATION, не required | канон §9.1: цена сильно влияет, только когда названа явно |
| слоты безопасности | **UNKNOWN — таблицы нет** | канон §7 «Still open»; OD §40.4 п.3 |

**Слот безопасности не выдуман здесь ни один.** Таблица загружается
из `ayla-knowledge`; сегодня она пуста. Поведение при пустой таблице — §11.4.

### 8.3 Функция удовлетворения

```
verdict(slot) =
  SATISFIED_KNOWN     ∃ e ∈ evidence : e.slot == slot
                        ∧ e.origin ∈ spec.satisfied_by_origins
                        ∧ ¬expired(e)                      # фильтр адаптера, P2
  SATISFIED_FLEXIBLE  spec.flexible_allowed ∧ slot объявлен FLEXIBLE подтверждённым событием
  UNSATISFIED         иначе
  NOT_REQUIRED        ¬required_when(…)
```

`model_signals` в этой функции **не участвуют**. Это и есть точка, где
инвариант EVIDENCE ORIGIN становится исполнимым кодом.

---

## 9. Величины: переиспользование DRF-1533

DRF-1533 уже задала границу дословно: «всё, что вычисляет число, — здесь;
всё, что сравнивает число с порогом и меняет поведение диалога, — там».
**«Там» — это настоящий документ.** DecisionReadiness ничего не вычисляет
заново; она потребляет три величины и сравнивает их с порогами.

| Величина | Кто вычисляет | Что означает | Как используется здесь |
|---|---|---|---|
| `separation ∈ [0,1]` | резолвер, DRF-1533 | нормированный разрыв между кандидатом 1 и 2 | `separation < τ_sep` → NEEDS_DISCRIMINATION |
| `completeness ∈ [0,1]` | резолвер, DRF-1533 | полнота контекста | Диагностика и аудит. **Не** источник состояния: обязательный контекст считается по §8.3, а не по этому числу |
| `conflicts ≥ 0` | резолвер, DRF-1533 | непустой refusal memo, недостающая часть составного запроса, сработавший регроундинг | `conflicts > 0` запрещает `READY` (§11.5) |

**Переименование, а не второе имя.** DRF-1533 и §8 канона — одно и то же
(`OPEN_DECISIONS.md` §40.3(б)). Канонические имена: состояния — из §8 канона;
величины — из DRF-1533. Третьего словаря не заводится.

**Соответствие четырёх порогов DRF-1533 пяти состояниям канона:**

| Порог DRF-1533 | Состояние v1.1 |
|---|---|
| HIGH → рекомендовать | `READY` |
| MEDIUM + высокое делегирование | `NEEDS_DISCRIMINATION` + `delegation=HIGH` → `allow_recommend` |
| MEDIUM + низкое делегирование → один вопрос | `NEEDS_DISCRIMINATION` |
| LOW → спрашивать (`separation = 0` по всему ярусу) | `INSUFFICIENT_EVIDENCE` |
| *(в DRF-1533 отсутствовало)* | `NEEDS_REQUIRED_CONTEXT`, `BLOCKED` |

Два состояния канона у DRF-1533 не имели аналога — это и есть приращение
настоящего документа, а не переписывание задачи.

---

## 10. Delegation

### 10.1 Что такое `delegation` и кто её поднимает

```
delegation ∈ {LOW, MEDIUM, HIGH}
```

Поднимается **только** явным действием человека: нажатие `SemanticOption`
с ролью `DELEGATE` (канон §13.1) или свободный текст, размеченный адаптером
как делегирующий и подтверждённый как `USER_TEXT`-свидетельство
(канон §13.2: «не знаю / выбери сам» = делегирование).

`ModelSignal` `delegation` **не поднимает** (§3.1 п.4). Модель не вправе решить,
что человек ей доверился.

`SURPRISE_ME` — **отдельная семантика открытия** (канон §8, последняя строка),
не общий обход недостатка свидетельств. Формально: `SURPRISE_ME` задаёт
`current_need`, но не меняет `delegation` и **не** снимает
`INSUFFICIENT_EVIDENCE`.

### 10.2 Инвариант DELEGATION CEILING

Структурная гарантия: **`delegation` не является аргументом функции состояния.**

```
readiness_state = f(evidence, candidates, safety, required_context_spec, availability)
allow_recommend = g(readiness_state, delegation, safety)
```

`f` не принимает `delegation` — значит `delegation` физически не может
превратить `NEEDS_REQUIRED_CONTEXT` в `READY`. Это сильнее, чем проверка
в конце: проверку можно забыть, отсутствующий параметр — нет.

`g` определена полностью:

```
g(state, delegation, safety):
    if safety.state in {CLARIFY, STOP, UNKNOWN}:      return False
    if state == READY:                                return True
    if state == NEEDS_DISCRIMINATION and delegation == HIGH:
                                                      return True
    return False
```

**Обязательные тесты инварианта** (каждый обязан краснеть при снятии правила):

| Тест | Вход | Ожидание |
|---|---|---|
| D1 | `state=NEEDS_REQUIRED_CONTEXT`, `delegation=HIGH` | `allow_recommend == False`, `reason ∋ DELEG_CEILING_REQUIRED_CONTEXT` |
| D2 | `state=NEEDS_DISCRIMINATION`, `delegation=HIGH`, `safety=CLARIFY` | `allow_recommend == False`, `reason ∋ DELEG_CEILING_SAFETY` |
| D3 | `state=NEEDS_DISCRIMINATION`, `delegation=HIGH`, `safety=STOP` | `allow_recommend == False`, `state == BLOCKED` |
| D4 | `state=INSUFFICIENT_EVIDENCE`, `delegation=HIGH` | `allow_recommend == False` |
| D5 (парная положительная) | `state=NEEDS_DISCRIMINATION`, `delegation=HIGH`, `safety=NORMAL` | `allow_recommend == True` |
| D6 (сигнатурный) | рефлексия сигнатуры `f` | `delegation` не входит в параметры |

D5 обязателен: без него легко «починить» так, что делегирование перестанет
работать вовсе, и никто не заметит.

---

## 11. Алгоритм: вычисление `readiness_state`

Порядок **нормативен**. Первое сработавшее правило определяет состояние;
функция тотальна.

```
def f(evidence, candidates, safety, spec, availability) -> ReadinessState:

  # 1. Безопасность — сильнее всего
  if safety.state == STOP:
      return BLOCKED(SAFETY_STOP)
  if safety.state == UNKNOWN or safety.state not in KNOWN_SAFETY_STATES:
      return BLOCKED(SAFETY_UNKNOWN)          # канон §6: unknown safety fails closed
  if safety.state == CLARIFY:
      return NEEDS_REQUIRED_CONTEXT           # см. §11.1

  # 2. Доступность входа — fail-closed до всякой арифметики
  if not availability.ledger_readable
     or not availability.probe_available
     or not availability.candidates_fresh
     or safety.evaluated_at_revision < state_revision:
      return BLOCKED(READINESS_INPUT_UNAVAILABLE)

  # 3. Структурная невозможность рекомендовать
  if candidates.recommendation_eligible_count == 0:
      return BLOCKED(CATALOG_NOT_RECOMMENDABLE
                     if candidates.visible_count > 0
                     else NO_ADMISSIBLE_CANDIDATES)

  # 4. Обязательный контекст
  unsatisfied = [s for s in spec if required_when(s) and verdict(s) == UNSATISFIED]
  if unsatisfied:
      return NEEDS_REQUIRED_CONTEXT

  # 5. Потребность ещё не заземлена
  if not grounded_need(evidence, candidates):
      return INSUFFICIENT_EVIDENCE

  # 6. Кандидаты не различимы
  if candidates.separation < policy.tau_separation or measures.conflicts > 0:
      return NEEDS_DISCRIMINATION

  # 7.
  return READY
```

### 11.1 Почему `CLARIFY` → `NEEDS_REQUIRED_CONTEXT`, а не `BLOCKED`

Канон §7.1: `CLARIFY` — «recommendation/execution is **paused until required
safety clarification is resolved**». Канон §8: `NEEDS_REQUIRED_CONTEXT`
— «MUST ask». Канон §7.2 и §8 одной и той же фразой: `delegation=HIGH`
не обходит ни required context, ни `CLARIFY`.

Два чтения (BLOCKED / NEEDS_REQUIRED_CONTEXT) **оба запрещают рекомендовать**,
поэтому это не продуктовая развилка и владельцу не выносится. Выбрано
`NEEDS_REQUIRED_CONTEXT`, потому что оно единственное описывает **путь выхода**
(задать безопасностный вопрос), а `BLOCKED` его не описывает. Следствие
для аудита: такие записи несут `reason ∋ SAFETY_CLARIFY_REQUIRED` и
`required_context.owner == SAFETY` — их всегда можно отделить от продуктового
required context.

### 11.2 `grounded_need` — детерминированно

```
grounded_need = ∃ e ∈ evidence : e.slot ∈ spec.need_slots
                 ∧ candidates.narrowed_by(e) == True
```
`narrowed_by` — факт от резолвера: применение этого свидетельства уменьшило
допустимое множество. Признак «потребность заземлена» — **сужение множества**,
а не наличие текста. Без него `INSUFFICIENT_EVIDENCE` был бы вкусовым.

### 11.3 Различение `INSUFFICIENT_EVIDENCE` и `NEEDS_DISCRIMINATION`

* `INSUFFICIENT_EVIDENCE` — потребность не заземлена; допустимое множество
  ещё не сужено ни одним подтверждённым свидетельством. Действие —
  **самый широкий полезный вопрос** (`kind = BROADENING`).
* `NEEDS_DISCRIMINATION` — потребность заземлена, множество сужено, но
  `separation` ниже порога. Действие — **один самый ценный различающий
  вопрос** (`kind = DISCRIMINATION`).

Практическое следствие из §41/DRF-1533: пока фактор точности не введён,
`separation ≡ 0`, и §11 будет отдавать `NEEDS_DISCRIMINATION` **на каждом
поиске**. Это ровно то лишнее спрашивание, от которого предостерегает
DRF-1533. Отсюда обязательная очерёдность внедрения — §18.

### 11.4 Поведение при пустой таблице безопасности (сегодняшнее состояние)

Матрицы сигналов нет (G7). Следствия, зафиксированные явно:

* Safety Engine, не имея правил, обязан возвращать `NORMAL` **только**
  если он отработал; отсутствие вычисления — это `UNKNOWN`, а `UNKNOWN`
  даёт `BLOCKED(SAFETY_UNKNOWN)` (канон §6: «unknown safety-critical
  semantics fail closed»). Молчаливый `NORMAL` по умолчанию **запрещён**.
* Пока таблица пуста, `spec` не содержит слотов с `owner = SAFETY`,
  и безопасность не порождает обязательного контекста. `health_screening`
  остаётся вне периметра движка до появления матрицы.
* Это делает §11 исполнимым уже сегодня и не требует выдумывать
  сигналы за владельца.

### 11.5 `conflicts > 0` запрещает `READY`

`conflicts` (DRF-1533) агрегирует непустой refusal memo, недостающую часть
составного запроса и сработавший регроундинг. Каждое из трёх означает,
что в контексте есть уже установленное «этого нет» или «я понял иначе».
Рекомендовать поверх неснятого конфликта — это класс дефекта DRF-1474
(бот обещает найти то, в чём только что отказал). Поэтому `conflicts > 0`
не блокирует, но опускает `READY` до `NEEDS_DISCRIMINATION`, где вопрос
обязан быть направлен на снятие конфликта (§13.3).

---

## 12. Взаимодействие с набором кандидатов

### 12.1 Что движок получает

```
CandidateSetSignature
├── digest                        str — стабильный хеш упорядоченного множества
├── visible_count                 int
├── recommendation_eligible_count int      ← канон §14, OD §40.2 п.5
├── cardinality                   int  (по recommendation_eligible)
├── ordered_ids[]                 (усечено до k, который будет показан)
├── separation                    float ∈ [0,1]     ← DRF-1533
├── spans(field) -> bool          city, price_band, provider, duration…
├── narrowed_by(evidence) -> bool
└── probe(evidence_delta) -> CandidateSetSignature      (§12.2)
```

Движок **не видит сырых баллов**. Он видит порядок, мощность, флаги
пригодности и нормированный `separation`. Это прямо следует из канона §8
(«raw score не публичный семантический API») и §9.3 («ranking output should
expose reason/provenance, not treat raw score as user-facing truth»).

### 12.2 `probe` — обязательный интерфейс резолвера

`probe(evidence_delta)` возвращает сигнатуру множества при гипотетическом
свидетельстве, **не меняя состояния**. Это единственный способ вычислить
правило допуска вопроса детерминированно (§13.3). Требование к резолверу
(трек C) формулируется здесь, но сам резолвер здесь не проектируется.

`probe` обязан быть чистым и воспроизводимым. Если `probe` недоступен —
`availability.probe_available = False` → `BLOCKED(READINESS_INPUT_UNAVAILABLE)`
(§11 шаг 2). Догадка вместо `probe` запрещена.

### 12.3 `catalog_visible ≠ recommendation_eligible`

Канон §14 и OD §40.2 п.5: немаппированную услугу можно показать и на неё
можно записаться **по прямому выбору человека**, но рекомендовать нельзя.
Отсюда:

* допустимое множество **для рекомендации** фильтруется по
  `recommendation_eligible`;
* `visible_count > 0 ∧ recommendation_eligible_count == 0`
  → `BLOCKED(CATALOG_NOT_RECOMMENDABLE)`. Поверхность вправе предложить
  явный выбор, но **молчаливая подмена запрещена** (канон §14, OD §40.2 п.5);
* отсутствие признака `recommendation_eligible` во входе — это `UNKNOWN`,
  а не `true`. Fail-closed: `BLOCKED(READINESS_INPUT_UNAVAILABLE)`.

**Замер, который делает это не теорией** (OD §40.3(д), §41): на пилоте
`contraindications` и `short_description` пусты на всех 265 услугах,
verified-маппинга нет ни у одной; при этом блок «Ayla подобрала тебе»
рекомендует **мастеров**, а не услуги, и гейт сегодня — «источник прислал
WHY», а не «маппинг VERIFIED». Движок этого не решает — вопрос владельца
OD §40.4 п.1 остаётся открытым и **блокирует включение**
`recommendation_eligible` как жёсткого фильтра.

---

## 13. NextBestQuestion

### 13.1 Форма

```
NextBestQuestion
├── question_id        str, стабильный (§13.2)
├── kind               REQUIRED_CONTEXT | DISCRIMINATION | BROADENING
│                      | SAFETY_CLARIFICATION
├── target_slots[]     упорядоченный канонически
├── mode               "confirm_one" | "choose_many" | "free"   ← §1.1, стык с кодом
├── options[]          SemanticOption (канон §13.1) — закрытое множество
├── impact_claim       ADMISSIBILITY | RANKING | EXECUTION_PARAM
├── impact_evidence    { probe_digest_before, probe_digests_after[] }
├── ask_reason         FIRST_ASK | REASK_<controlled reason>      (§13.5)
└── semantics_version  int
```

`mode` берётся из существующих значений (§1.1). Четвёртого механизма нет.
Соответствие:

| `kind` | типовой `mode` | почему |
|---|---|---|
| `REQUIRED_CONTEXT` | `confirm_one`, при отсутствии закрытого домена — `free` | ответ обязан быть однозначным |
| `DISCRIMINATION` | `confirm_one` | различающий вопрос по определению выбирает одну ветку |
| `BROADENING` | `free` или `confirm_one` по верхнеуровневым группам | домен ответа широк |
| `SAFETY_CLARIFICATION` | `confirm_one` | безопасность не допускает свободной интерпретации |

`choose_many` **никогда не выводится**, только объявляется явно в
`QuestionCatalog` — то же правило, что уже действует в
`normalize_clarification_mode()`.

Опции всегда включают `ESCAPE` и, где применимо, `DELEGATE` — и они
**не исчезают при усечении списка** (канон §13.5; OD §40.3(г): владелец
поднял предел до семи, но требование неисчезновения escape/делегирования
остаётся в силе).

### 13.2 Стабильный `question_id`

```
question_id = b16( sha256(
      kind
    | "\x1f" | join(sorted(target_slots), ",")
    | "\x1f" | discriminator_key
    | "\x1f" | str(semantics_version)
) )[:16]
```

Свойства, каждое из которых нужно:

* **не зависит от формулировки** — иначе перефразированный моделью тот же
  вопрос читался бы как новый, и DRF-1542 остался бы возможен;
* **не зависит от порядка и от локали опций**;
* **зависит от множества слотов** — вопрос про `body_area + onset_context`
  и вопрос только про `body_area` имеют **разные** id. Это намеренно:
  сузившийся вопрос — другой вопрос;
* **зависит от `semantics_version`** — единственный контролируемый способ
  «сбросить» реестр, и он требует явного изменения каталога, а не деплоя;
* **не зависит от `policy_version`** — изменение порогов не даёт права
  переспросить.

`discriminator_key` — имя оси различения из `QuestionCatalog`
(например `"price_band"`, `"duration"`), пустая строка для
`REQUIRED_CONTEXT`.

**SURFACE-INDEPENDENT:** `question_id` не содержит `surface`. Один и тот же
семантический вопрос в MAX-чате и в Mini App имеет один id и **один реестр**.
Ответ, данный в Mini App, закрывает вопрос в чате. Обратное — тоже.

### 13.3 Правило допуска вопроса (канон §8, дословно)

> Следующий вопрос разрешён, только если ответ может изменить допустимость,
> ранжирование или обязательные параметры исполнения.

Вычислимая форма. Для вопроса `Q` со слотом `S` и объявленным доменом
ответа `D(S)` (закрытое множество из `QuestionCatalog`; для `mode="free"` —
объявленный набор классов ответа):

```
changes_admissibility(Q) =
    ∃ d₁,d₂ ∈ D(S) : probe(S=d₁).eligible_ids ≠ probe(S=d₂).eligible_ids

changes_ranking(Q) =
    ∃ d₁,d₂ ∈ D(S) : probe(S=d₁).ordered_ids[:k] ≠ probe(S=d₂).ordered_ids[:k]

changes_execution_param(Q) =
    S ∈ execution.required_params  ∧  slot(S).state == UNKNOWN

ask_allowed(Q) = changes_admissibility ∨ changes_ranking ∨ changes_execution_param
```

Три следствия, которые стоит назвать:

1. `slot(S).state == FLEXIBLE` **не** даёт `changes_execution_param` —
   человек уже сказал «неважно» (канон §16.2). Спрашивать заново запрещено.
2. Если `probe` показывает, что все ответы дают одинаковое множество
   и одинаковый порядок — вопрос **запрещён**, даже если он «логичный».
   Это и есть формальный запрет пяти экранов DRF-1542: там ни один
   из вопросов ничего не менял.
3. Вопрос `kind = SAFETY_CLARIFICATION` **освобождён** от `ask_allowed`:
   его допустимость определяет политика безопасности, а не влияние
   на ранжирование. Освобождение узкое и явное.

### 13.4 Реестр заданных вопросов

Форма — по образцу `refusal_memo` (§1.2), поле то же, ключ непересекающийся:

```
Conversation.skill_state["decision_readiness"] = {
  "v": 1,
  "asked": [
    { "qid": str,
      "kind": str,
      "slots": [str],
      "semantics_version": int,
      "policy_version": int,
      "asked_at": iso8601,
      "asked_at_revision": int,
      "ask_count": int,
      "resolved": bool,
      "resolved_by_evidence_id": str | null,
      "resolved_at_revision": int | null,
      "last_reask_reason": str | null }
  ]
}
```

Правила ведения:

* **Ключ записи — `(qid, asked_at_revision)`.** Повторная доставка того же
  события (ретрай вебхука, дубль колбэка) **не создаёт второй записи**
  и не увеличивает `ask_count`.
* Список ограничен (`_MAX_ENTRIES`, по образцу `refusal_memo` — 5 недостаточно,
  берётся 20: реестр вопросов длиннее реестра отказов; величина —
  CONTROLLED_POLICY).
* TTL записи — по слоту: `STABLE` слоты живут до конца сессии
  (канон §4.1, TTL бездействия 2 часа), `VOLATILE` — по своему
  `ttl_seconds`. Прецедент: `booking_context.py:64` — 900 с для
  предпочтения времени.
* **Контракт записи — fail-closed, а не best-effort.** Это отличие
  от `refusal_memo`, и оно намеренное: там потеря записи стоила одного
  повторного отказа, здесь она стоит цикла DRF-1542. Не удалось записать
  или прочитать → `availability.ledger_readable = False`
  → `BLOCKED(READINESS_INPUT_UNAVAILABLE)` → **вопрос не задаётся**.

### 13.5 Controlled правило повтора

Повтор `question_id` разрешён **только** по одной из перечисленных причин.
Список закрыт; расширение — изменение `spec_version`.

| Причина | Условие |
|---|---|
| `REASK_ANSWER_RETRACTED` | человек явно отозвал/исправил свидетельство, закрывшее слот. Провенанс исходного и отзыва сохраняется (канон §7.2) |
| `REASK_ANSWER_EXPIRED` | слот `VOLATILE`, `ttl_seconds` истёк |
| `REASK_SEMANTICS_CHANGED` | `semantics_version` вопроса в каталоге увеличен |
| `REASK_CANDIDATE_SET_CHANGED` | `probe` показывает, что слот **снова** различает: `ask_allowed(Q)` стал истинным, будучи ложным на момент прошлого ответа |
| `REASK_SAFETY_REEVALUATION` | `safety.state` перешёл в `CLARIFY` и этот вопрос — его требуемое уточнение |

Иначе — вопрос подавляется, `reason ∋ ASK_SUPPRESSED_ALREADY_ASKED`.

**Потолки (CONTROLLED_POLICY, значения по умолчанию):**

```
MAX_ASKS_PER_QUESTION_ID              = 2   # первый ask + один controlled re-ask
MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE = 2
```

Второй потолок считает подряд идущие ходы-вопросы, не давшие **ни одного
нового подтверждённого свидетельства** по целевым слотам. При исчерпании —
`BLOCKED(ASK_BUDGET_EXHAUSTED)`.

`MAX_ASKS = 2` не выдуман: он следует из правила допуска. Вопрос, на который
ответили и который после ответа ничего не меняет, по определению больше
не разрешён; единственный законный второй ask — тот, что оправдан одной
из пяти причин выше. Третий ask по той же причине означал бы, что причина
не сработала.

**Модель не запускает re-ask сама.** `ask_reason` вычисляется движком;
`ModelSignal` не входит ни в одно из пяти условий.

### 13.6 Выбор одного вопроса из нескольких допустимых — детерминированно

Среди `{Q : ask_allowed(Q) ∧ повтор разрешён или это первый ask}`:

1. приоритет `kind`: `SAFETY_CLARIFICATION` > `REQUIRED_CONTEXT` >
   `DISCRIMINATION` > `BROADENING`;
2. внутри `kind` — максимум `expected_separation_gain`
   = `max_{d∈D(S)} probe(S=d).separation − candidates.separation`;
3. при равенстве — меньший `|D(S)|` (короче путь к ответу);
4. при равенстве — лексикографически меньший `question_id`.

Полный порядок, без случайности. Одинаковый вход → одинаковый вопрос.

---

## 14. Как этот контракт делает DRF-1542 невозможным

DRF-1542 — живой дефект: `health_screening` выдал один и тот же экран
пять раз подряд. Механизм по задаче: (1) контекст строится из слов модели
(`nutrition_global.py:269–286`), (2) навык не помнит, что спрашивал,
(3) промпт обязывает звать навык первым — и эта строка правильная.

Проход тех же пяти ходов через настоящий контракт:

| Ход | Что было | Что происходит по контракту |
|---|---|---|
| 07:52:28 жалоба | экран | Safety извлекает сигналы (LLM_ALLOWED) → `CLARIFY` → `NEEDS_REQUIRED_CONTEXT`, вопрос `Q₁(body_area, onset_context)`, запись в реестр `(qid₁, rev₁)` |
| 07:52:55 назвал обстоятельство | **тот же экран** | `USER_TEXT`-свидетельство закрывает `onset_context`. Пересчёт: неудовлетворён только `body_area`. Следующий вопрос имеет **другой** `target_slots` ⇒ **другой `question_id`**. Байт-в-байт тот же экран невозможен |
| 07:53:13 назвал место | **тот же экран** | `body_area` закрыт. `Q₁` и `Q₂` помечены `resolved`. Повтор запрещён: ни одна из пяти причин §13.5 не выполнена |
| 10:50:23 «ну и как тебе донести…» | **тот же экран** | Модель снова отдаёт `symptom_text="болит поясница"`. Это `ModelSignal`, `origin = MODEL_INFERENCE` — он **не закрывает и не переоткрывает** слот и не даёт права переспросить. Вето навыка считается по `SemanticUserEvent.raw_text` (§6, строка «Присвоение origin»), а не по аргументу модели |
| 10:50:37 «Что ты понимаешь?» | **тот же экран** | То же. Дополнительно уже сработал бы потолок: `MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE = 2` → `BLOCKED(ASK_BUDGET_EXHAUSTED)` |

**Три независимых барьера**, каждый достаточен сам по себе:

* **B1 — EVIDENCE ORIGIN.** Пересказ модели не является свидетельством
  и не может ни закрыть слот, ни его переоткрыть (§3.1).
* **B2 — QUESTION LOOP.** Реестр знает, что вопрос задан и отвечен;
  повтор требует одной из пяти перечисленных причин (§13.4–13.5).
* **B3 — правило допуска.** Вопрос, ответ на который ничего не меняет
  по `probe`, запрещён вовсе (§13.3). Все пять экранов DRF-1542
  проваливают этот тест.

Плюс потолок §13.5 делает арифметически невозможным число «пять».

**Границы утверждения, честно:** контракт делает дефект невозможным
*для решений, проходящих через движок*. `health_screening` попадёт под него
только когда появится матрица безопасности (§11.4) и навык начнёт получать
обязательный контекст от Safety Engine. До этого DRF-1542 чинится своей
задачей, своим исполнителем и своим кодом — **который здесь не трогается**.
Барьер B1 совпадает с пунктом B той задачи; барьер B2 — с пунктом A.
Противоречия нет, дублирования работы нет.

---

## 15. Fail-closed

### 15.1 Единый принцип

Неизвестное — не ноль, не `false`, не умолчание. Любая неопределённость
входа даёт **отказ от рекомендации и отказ от вопроса**, а не догадку.

Отказ от вопроса — существенная половина. «Не знаю → спрошу» выглядит
безопасно и является ровно тем поведением, которое породило DRF-1542.

### 15.2 Таблица

| Ситуация | `readiness_state` | `reason_code` |
|---|---|---|
| `safety.state` неизвестен / не вычислен | `BLOCKED` | `SAFETY_UNKNOWN` |
| `safety` вычислен для более старой ревизии (P3) | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| Реестр вопросов нечитаем/незаписываем | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| `probe` недоступен | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| Кандидаты несвежие | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| `recommendation_eligible` отсутствует как признак | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| `recommendation_eligible_count == 0`, `visible_count > 0` | `BLOCKED` | `CATALOG_NOT_RECOMMENDABLE` |
| Допустимых кандидатов нет вовсе | `BLOCKED` | `NO_ADMISSIBLE_CANDIDATES` |
| `question_id` вне `QuestionCatalog` | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| Потолок вопросов исчерпан | `BLOCKED` | `ASK_BUDGET_EXHAUSTED` |
| `policy_version` входа ≠ загруженной | `BLOCKED` | `READINESS_INPUT_UNAVAILABLE` |
| Свидетельство с неизвестным `origin` | отбрасывается | `EVID_ORIGIN_UNKNOWN_DISCARDED` |
| `slot` со значением `null` без различения UNKNOWN/FLEXIBLE | трактуется как `UNKNOWN` | `SLOT_NULL_TREATED_UNKNOWN` |

### 15.3 Что делает поверхность при `BLOCKED` — граница документа

Движок гарантирует: **рекомендации не будет и повторного вопроса не будет.**
Что именно человек увидит вместо этого (углублённый мини-FSM или ход,
отданный модели с оговоркой «я не врач»), — **открытый вопрос владельца,
уже поставленный в DRF-1542** («Вопрос владельцу — в объём не входит»).
Здесь он **не решается и не дублируется**.

Единственное, что фиксируется нормативно: любой выбранный вариант обязан
соблюдать канон §7 — на `STOP` нет диагноза, нет обхода «всё равно записать»,
нет CTA на wellness-услугу, противоречащую блокировке.

---

## 16. Реестр `reason_codes`

Закрытый, версионируемый вместе с `spec_version`. Аналитика ключуется
по кодам, не по текстам (канон §13.5). Каждый выход несёт ≥ 1 код;
коды сортируются лексикографически (детерминизм, §17.1).

```
STATE_READY
STATE_NEEDS_DISCRIMINATION
STATE_NEEDS_REQUIRED_CONTEXT
STATE_INSUFFICIENT_EVIDENCE
STATE_BLOCKED

SAFETY_STOP
SAFETY_CLARIFY_REQUIRED
SAFETY_CAUTION_CONSTRAINED
SAFETY_UNKNOWN

BLOCK_NO_ADMISSIBLE_CANDIDATES
BLOCK_CATALOG_NOT_RECOMMENDABLE
BLOCK_READINESS_INPUT_UNAVAILABLE
BLOCK_ASK_BUDGET_EXHAUSTED

REQ_CONTEXT_UNSATISFIED:<slot>
REQ_CONTEXT_SATISFIED_FLEXIBLE:<slot>
REQ_CONTEXT_NOT_REQUIRED:<slot>

ASK_ALLOWED_ADMISSIBILITY
ASK_ALLOWED_RANKING
ASK_ALLOWED_EXECUTION_PARAM
ASK_SUPPRESSED_NO_IMPACT
ASK_SUPPRESSED_ALREADY_ASKED
ASK_SUPPRESSED_SLOT_FLEXIBLE
ASK_REASK_ANSWER_RETRACTED
ASK_REASK_ANSWER_EXPIRED
ASK_REASK_SEMANTICS_CHANGED
ASK_REASK_CANDIDATE_SET_CHANGED
ASK_REASK_SAFETY_REEVALUATION

EVID_MODEL_SIGNAL_NOT_EVIDENCE
EVID_ORIGIN_UNKNOWN_DISCARDED
EVID_EXPIRED_DISCARDED
EVID_RETRACTED

DELEG_APPLIED_HIGH
DELEG_CEILING_REQUIRED_CONTEXT
DELEG_CEILING_SAFETY
DELEG_CEILING_INSUFFICIENT_EVIDENCE

MEASURE_SEPARATION_BELOW_TAU
MEASURE_CONFLICTS_PRESENT

SLOT_NULL_TREATED_UNKNOWN
```

### 16.1 Пороги (CONTROLLED_POLICY)

| Порог | Умолчание | Статус |
|---|---|---|
| `tau_separation` | **UNKNOWN** | Числа §8 канона заданы словами. DRF-1519 прямо требует вынести выбор владельцу **до** построения. Не выдумывается здесь |
| `N_broad` (порог «множество ещё широкое») | **UNKNOWN** | там же |
| `k` (глубина сравнения порядка в `changes_ranking`) | равен числу показываемых карточек | производное, не решение |
| `MAX_ASKS_PER_QUESTION_ID` | 2 | производное от правила допуска (§13.5) |
| `MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE` | 2 | там же |
| `_MAX_LEDGER_ENTRIES` | 20 | техническая граница по образцу `refusal_memo` |

Два UNKNOWN — **не пробел этого документа**, а уже поставленный вопрос
DRF-1519. Дублировать его владельцу нельзя (правило «не заводить дубликаты»).

### 16.2 Матрица безопасности

Содержимое `RequiredContextSpec` для `owner = SAFETY` — **UNKNOWN**,
владелец не давал (канон §7 «Still open», OD §40.4 п.3). Форма задана (§8.1),
поведение при пустой таблице задано (§11.4). Документ исполним и без неё.

---

## 17. Идемпотентность и детерминизм

### 17.1 `evaluate` — чистая функция

* Ни одного вызова LLM.
* Ни одного чтения часов. Просрочка вычисляется адаптером входа (P2).
* Ни одного источника случайности. Разведение ничьих — лексикографическое
  (§13.6 п.4). Ротация экспозиции при истинных ничьих (канон §9.1)
  принадлежит резолверу, не движку.
* Все множества упорядочиваются каноническим сравнением перед хешированием
  и перед выводом.
* `reason_codes` сортируются.
* Ни одного чтения глобального состояния — реестр приходит во входе.

**Тест детерминизма:** тот же `ReadinessInput` → побайтово тот же
`ReadinessOutput`, включая `question_id` и порядок `reason_codes`.
Тысяча прогонов, ноль расхождений. Это тот самый признак,
который DRF-1519 назвала главным доказательством замены самооценки модели.

### 17.2 Проверка отсутствия LLM в контуре решения

Замер по коду, повторяющий формулировку DRF-1519: число мест, где решение
«спросить или рекомендовать» принимается по самооценке модели, — **ноль**.
Форма проверки: модуль движка не импортирует ни один провайдер LLM;
статическая проверка импортов в CI.

### 17.3 Идемпотентность

```
readiness_key = sha256(
    spec_version | policy_version | state_revision
  | evidence_digest | candidate_set.digest | safety_digest
  | ledger_digest | delegation
)
```

* Повторный вызов с тем же ключом возвращает тот же результат
  и **не создаёт второй записи в реестре**.
* Запись в реестр ключуется `(question_id, asked_at_revision)` — дубль
  доставки не увеличивает `ask_count` (§13.4). Это защищает потолки
  от ретраев транспорта.
* `state_revision` в ключе делает устаревший колбэк отличимым:
  классификация `CURRENT / REVALIDATABLE / INVALID` (канон §13.4)
  принадлежит транспорту, движок лишь получает свежий вход.

---

## 18. Версионирование и очерёдность внедрения

### 18.1 Три независимые версии

| Версия | Что версионирует | Где видна |
|---|---|---|
| `spec_version` (semver) | форму контракта: поля, перечисления, реестр кодов | выход, аудит |
| `policy_version` (монотонное целое) | пороги и содержимое `RequiredContextSpec` | выход, аудит, каждая запись реестра |
| `semantics_version` (на вопрос) | смысл конкретного вопроса | входит в `question_id` |

Правила:

* Смена `policy_version` **сама по себе не даёт права переспросить**
  (§13.2 — она не входит в `question_id`).
* Выходы под разными `policy_version` **не сравнимы** в аналитике;
  каждая запись реестра хранит свою.
* Расширение реестра `reason_codes` или списка причин повтора —
  это `spec_version` major/minor, не тихая правка.
* Перечисления не дублируются между репозиториями (канон §6).

### 18.2 Очерёдность внедрения — нормативно

DRF-1533 сформулировала это первой и она права: сегодня `separation ≡ 0`.
Если ввести пороги раньше фактора точности, **любой порог даст «спрашивать»
на каждом поиске** — то самое лишнее спрашивание, которое канон запрещает.

Владелец записал то же условие прямо (эпик DRF-1529): «Включать только
после точности, измерения различимости и уточняющего вопроса».
Замер оттуда же: в пяти запросах из шести весь результат в одном ярусе,
порядок решает алфавит (`apps/marketplace/discovery.py:1333`), а срез
в пять происходит в SQL (`:1364`) — на «массаж» троих мастеров из восьми
человек не увидит никогда. Пока это так, различающий вопрос не на чем
строить.

```
1. Точность совпадения вместо счётчика слов        DRF-1529 п.1  (5 SP, одобрено)
2. separation / completeness / conflicts в логе    DRF-1533      (3 SP)
3. state_revision                                  G1 — задачи нет
4. ConfirmedEvidence / ModelSignal + адаптеры      G2 — задачи нет
5. Реестр вопросов + стабильный question_id        G3, G4 — задачи нет
6. Движок за флагом, теневой режим: только аудит, без влияния на диалог
7. Пороги от владельца (DRF-1519) → включение
```

Существующая **DRF-1531** («различающий вопрос словами каталога вместо
сортировки неразличимого», 5 SP, Todo) — это шаг рендера вопроса. Настоящий
документ задаёт, **какой** вопрос допустим и когда его нельзя задавать;
DRF-1531 задаёт, **из чего** он собирается. Дубликата заводить не нужно:
DRF-1531 расширяется правилом допуска §13.3 и реестром §13.4.

Шаги 1–2 — чужие задачи, здесь не переписываются. Шаг 6 обязателен:
включение движка **до** замера на живом контуре повторит ошибку §41
(«скорер отдаёт, но не то»).

---

## 19. Auditability

Каждая оценка порождает запись `DecisionEvidence` — канон §8:
«Every decision should carry `DecisionEvidence` / reason codes for WHY
and auditability».

```
DecisionEvidence
├── readiness_evaluation_id
├── readiness_key                   (§17.3)
├── state_revision, surface, mode
├── readiness_state, allow_recommend, delegation
├── reason_codes[]
├── measures { separation, completeness, conflicts }
├── required_context[]  { slot, owner, verdict, satisfied_by_evidence_id }
├── evidence_considered[] { evidence_id, slot, origin, source_ref }   ← происхождение видно
├── model_signals_rejected[] { slot, extractor_id, reason }           ← и что отвергнуто
├── candidates { digest, visible_count, eligible_count, cardinality }
├── safety { state, rule_id, policy_version, signals[], restrictions[] }
├── question { question_id, kind, mode, target_slots, impact_claim,
│              ask_reason, ask_count_after, suppressed_reason }
├── spec_version, policy_version
└── produced_at
```

Требования:

* **Воспроизводимость.** По записи `evaluate` обязан выдать то же состояние.
  Запись содержит все входы либо их дайджесты; при расхождении дайджеста
  запись помечается `NOT_REPLAYABLE` — молчаливого расхождения быть не может.
* **Происхождение никогда не теряется.** `evidence_considered.origin`
  и `model_signals_rejected` — обязательные поля. По записи всегда видно,
  что решение построено на человеке, а не на пересказе.
* **Отвергнутое видно так же, как принятое.** Без `model_signals_rejected`
  дефект класса DRF-1542 в аудите не отличим от нормы.
* **Персистенция** — бэкенд Ayla, рядом с `DecisionEvidence` рекомендации
  (канон §9.3, §10.1). Форма хранения здесь не проектируется.
* **PII.** Запись хранит `source_ref` (идентификаторы), не текст реплик.
  Текст — в истории диалога с её собственным жизненным циклом.

---

## 20. Сводка инвариантов

| Инвариант | Формулировка | Структурная гарантия | Тест |
|---|---|---|---|
| **EVIDENCE ORIGIN** | Вывод модели не становится свидетельством человека | Разные типы; нет конверсии; функция §8.3 читает только `ConfirmedEvidence` | §3.1 |
| **QUESTION LOOP** | Каждый семантический вопрос имеет стабильный id; состояние знает задан/отвечен; повтор только по controlled reason | `question_id` от семантики, не от текста; реестр fail-closed; закрытый список из пяти причин; потолки | §13.4–13.5, §14 |
| **DELEGATION CEILING** | `HIGH` может разрешить при `NEEDS_DISCRIMINATION`, но никогда не обходит required context, `CLARIFY`, `STOP` | `delegation` не является параметром `f` | D1–D6, §10.2 |
| **NO MODEL CONFIDENCE** | Готовность не выводится из самооценки модели | `evaluate` не импортирует провайдер LLM; во входе нет чисел от модели | §17.1–17.2 |
| **ASK IMPACT** | Вопрос разрешён, только если ответ может изменить допустимость, ранжирование или обязательный параметр исполнения | Вычисляется через `probe`, а не суждением | §13.3 |
| **FAIL CLOSED** | Неизвестное не догадывается; отказ и от рекомендации, и от вопроса | Таблица §15.2 тотальна | §15 |
| **SURFACE-INDEPENDENT** | Один семантический вопрос — один id и один реестр на всех поверхностях | `surface` не входит в `question_id` | §13.2 |
| **UNKNOWN ≠ FLEXIBLE** | `null` не схлопывает два разных состояния слота | Трёхзначное состояние слота | §3.2, §15.2 |

---

## 21. Прохождение пяти gates

| Gate | Вердикт | Обоснование |
|---|---|---|
| **1. Canon reconciliation** | **Пройден** | Пять состояний, правило допуска вопроса, `delegation=HIGH`, `SURPRISE_ME`, `DecisionEvidence` — из §8 дословно. Стыки: §7 (4 состояния, `CLARIFY`/`STOP` не обходятся), §9 (город-фильтр, рейтинг вторичен, ничьи), §13 (`SemanticOption`, escape не исчезает, `state_revision`), §14 (`catalog_visible ≠ recommendation_eligible`), §16.2 (`KNOWN/UNKNOWN/FLEXIBLE`). Одно место потребовало вывода — `CLARIFY → NEEDS_REQUIRED_CONTEXT`; рассуждение и следствие для аудита в §11.1. Владельцу не выносится: оба чтения запрещают рекомендацию, продуктовой развилки нет |
| **2. Code reality** | **Пройден с оговоркой** | Существующее переиспользовано и проверено чтением: три режима вопроса (§1.1, с исправлением имени и номеров строк), `refusal_memo` (§1.2), `Conversation.skill_state` (§1.2), `_matched_services` (§1.5), `_confidence_floor_reason` (§1.3). Оговорка: документ **опирается на шесть отсутствующих сегодня вещей** (G1–G5, G8) и все они перечислены как предусловия §4.1 и очерёдность §18.2. Ни одна не выдана за существующую |
| **3. Cross-contract consistency** | **Частично — на момент написания** | Внутренне согласован с канонической номенклатурой (`SemanticOption`, `StructuredDecision`, `delegation`, `state_revision`, `decision_id`, `KNOWN/UNKNOWN/FLEXIBLE`, `recommendation_eligible`), с DRF-1533 (величины) и DRF-1519 (пороги). **Полная сверка с треками B/C/D невозможна до появления их документов** — требуется совместный проход по: `Goal` как источник `PROMOTED_MEMORY`-свидетельства (B), интерфейс `probe` и `CandidateSetSignature` (C), `execution.required_params` для Plan (D). Пока сверки нет, gate не может быть объявлен полностью пройденным |
| **4. Failure behavior** | **Пройден** | §15.2 покрывает: UNKNOWN, устаревшее (P3, TTL), недоступное (`probe`, реестр, кандидаты), конфликтующее (§11.5), заблокированное безопасностью (§11 шаг 1, §11.4), отсутствующий маппинг (§12.3), отсутствующее свидетельство (§8.3, §11 шаги 4–5). Таблица тотальна: функция `f` возвращает состояние на любом входе |
| **5. Implementability** | **Пройден для механизма; два входа остаются UNKNOWN** | Разработчик может написать `f`, `g`, `question_id`, реестр, правило повтора и таблицу fail-closed, не принимая ни одного продуктового решения. Но **включить** движок нельзя без: (1) `tau_separation` / `N_broad` — вопрос DRF-1519, уже у владельца; (2) матрицы безопасности — OD §40.4 п.3, уже у владельца. Ни то, ни другое здесь не выдумано и не продублировано. Поэтому статус документа: **реализуем, не включаем** — §18.2 шаг 6 (теневой режим) существует именно для этого разрыва |

---

## 22. Вопросы владельцу

Оба — настоящие развилки: два варианта совместимы с каноном, выбор меняет
поведение для человека. Пороги и матрица безопасности сюда **не вынесены**:
они уже стоят в DRF-1519 и OD §40.4 п.3, дубликаты заводить нельзя.

### Вопрос 1. Может ли подтверждённая память закрывать обязательный контекст безопасности

1. **Что не определено.** `PROMOTED_MEMORY` входит в `CONFIRMABLE_ORIGINS`.
   Вправе ли она удовлетворять слот с `owner = SAFETY`, или безопасностный
   слот всегда требует свежего `USER_TEXT` / `USER_ACTION` в текущей сессии.
2. **Почему канон не даёт ответа.** §11 разрешает контролируемую promotion
   и §1 говорит, что память «сокращает лишние вопросы»; §7 требует
   пересчитывать безопасность после каждого значимого события, но не говорит,
   считается ли уже сохранённый факт «пересчитанным». Обе трактовки
   канону не противоречат.
3. **Вариант A.** Память закрывает безопасностный слот. Человек, однажды
   сказавший про хроническую травму, не отвечает на это каждую сессию.
4. **Вариант B.** Безопасностный слот закрывается только текущим явным
   ответом. Память может лишь **предзаполнить** вопрос
   (`confirm_one` с готовым ответом), но человек обязан подтвердить.
5. **Последствия.** A — меньше трения, но риск: состояние здоровья
   изменилось, а система действует по прошлогоднему факту. B — вопрос
   про здоровье на каждой сессии; при варианте B это **не** цикл DRF-1542
   (id один, ответ один, повтор межсессионный), но это ощутимое трение.
6. **Рекомендация.** B с предзаполнением: один тап на подтверждение вместо
   набора текста. Стоимость ошибки в A асимметрична — она в сторону
   рекомендации, противопоказанной сегодня.
7. **Заблокировано до решения.** Поле `satisfied_by_origins` для группы
   `owner = SAFETY` в `RequiredContextSpec`. Остальной механизм не блокирован.

### Вопрос 2. Что происходит с сессией, когда человек не отвечает на обязательный контекст

1. **Что не определено.** Канон говорит `NEEDS_REQUIRED_CONTEXT` → «MUST ask».
   Он не говорит, что делать, если спросили дважды (потолок §13.5) и ответа
   нет — человек пишет о другом или молчит.
2. **Почему канон не даёт ответа.** §8 описывает состояния и действия,
   но не исчерпание. `BLOCKED` описан как «recommendation prohibited»,
   без срока.
3. **Вариант A. Терминально для сессии.** Слот остаётся `UNKNOWN`,
   рекомендации в этой сессии нет до конца TTL (2 часа). Разговор
   продолжается, Ayla отвечает, но не рекомендует.
4. **Вариант B. Деградация до явного выбора.** Рекомендаций нет, но
   человеку предлагается **каталог по прямому выбору** — то самое,
   что §14 разрешает для немаппированных услуг: показать и записать
   можно, рекомендовать нельзя.
5. **Последствия.** A — честнее и проще, но человек, пришедший записаться,
   упирается в стену и уходит. B — человек получает результат, но легко
   спутает «вот список» с «Ayla советует»; требует явной разницы
   в формулировке и в `Recommendation`-объекте (список по выбору
   **не** порождает `recommendation_id`).
6. **Рекомендация.** B, при жёстком условии: выдача по явному выбору
   не создаёт `Recommendation` и не пишет `DecisionEvidence`
   с `allow_recommend = true`. Иначе аналитика начнёт считать
   вынужденный список за рекомендацию.
7. **Заблокировано до решения.** Поведение поверхности при
   `BLOCKED(ASK_BUDGET_EXHAUSTED)`. Само состояние и потолок не блокированы.

---

## 23. Что осталось UNKNOWN

Ни одно из перечисленного не заменено догадкой.

| UNKNOWN | Владелец вопроса |
|---|---|
| `tau_separation`, `N_broad` | DRF-1519 (уже поставлен) |
| Матрица сигналов безопасности и продуктовые формулировки | OD §40.4 п.3 / канон §7 «Still open» |
| Пилотный каталог: VERIFIED по умолчанию или блокировка рекомендаций | OD §40.4 п.1 |
| Что человек видит при `BLOCKED` после исчерпания вопросов | §22 вопрос 2 + DRF-1542 (не дублируется) |
| Может ли память закрывать безопасностный слот | §22 вопрос 1 |
| Кто хранит `ResumeSummary` | OD §40.4 п.2 — влияет на восстановление реестра после перерыва |
| Проверенные лимиты MAX | OD §40.4 п.4 — влияет на усечение `options[]`, но не на `question_id` |
