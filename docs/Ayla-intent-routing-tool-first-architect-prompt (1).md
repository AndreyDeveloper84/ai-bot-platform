# Ayla — промпт для оркестратора-архитектора: Tool-First Concierge Architecture

Ты работаешь как **оркестратор и системный архитектор Ayla**.

Нужно принять архитектурные решения по слою понимания пользовательских намерений и скорректировать ближайшие capabilities — прежде всего **DRF-1032 «Мои визиты / Записаться ещё»** — так, чтобы не перестраивать routing перед Controlled Pilot, но и не строить новые возможности как одноразовые regex-ветки.

## 1. Исходный факт

Проверка текущего deterministic intent detector дала плохой результат: из 13 естественных формулировок просмотра записей корректно распознаны только 4.

Не ловятся, среди прочего:

- «мои записи покажи»;
- «мои записи»;
- «покажи записи»;
- «мои визиты»;
- «что у меня записано»;
- «мои брони».

Работают в основном формулировки, близкие к существующим тестам: «покажи мои записи», «когда я записан».

Ранее аналогично обнаруживались lexical/morphology/order gaps: русское «менеджер» не ловилось при наличии только `manager`; «визит» не покрывается корнем `запис`; изменение порядка слов ломает правила.

## 2. Главный архитектурный сигнал

При фразе «Мои записи покажи» detector не понял intent, но LLM fallback поняла его правильно и ответила, что не может показать записи.

Получилась инверсия:

```text
User
 ↓
Regex detector — не понимает
 ↓
LLM — понимает
 ↓
нужного tool нет
 ↓
Ayla не может выполнить действие
```

Проблема не только в regex. Слой с исполнением плохо понимает язык, а слой с пониманием не имеет достаточных инструментов.

## 3. Не делать вывод «перевести всё на LLM»

Deterministic routing нужен из-за latency, стоимости, предсказуемости, outages внешнего LLM/proxy, безопасности, callback/button events, state-machine transitions и escalation invariants.

В проекте уже были длительные зависания и недоступность LLM/proxy. Поэтому LLM не должна становиться единственной точкой работоспособности Ayla.

## 4. Почему нельзя менять фундамент перед Pilot

Controlled Pilot должен проверять продуктовую петлю на живых людях. Если одновременно заменить intent architecture, станет трудно отделить product failure от routing failure.

**Owner ruling: до Controlled Pilot фундаментальный natural-language routing не менять.**

---

# Owner Decision OD-IR1 — Pilot routing

До Controlled Pilot текущий deterministic routing остаётся рабочим механизмом.

Нужно:

1. закрыть известные lexical/order/synonym gaps;
2. добавить regression corpus естественных формулировок;
3. проверить false positives/collisions;
4. не пытаться построить «идеальный regex NLP»;
5. не переписывать routing architecture.

Минимальный corpus для «Мои записи»:

```text
покажи мои записи
мои записи покажи
мои записи
покажи записи
мои визиты
покажи мои визиты
когда я записан
что у меня записано
мои брони
покажи мои брони
куда я записан
к кому я записан
есть ли у меня записи
```

Расширить corpus для pilot-critical intents: booking, appointments, history, repeat, human escalation, cancel, reschedule, help.

Проверять не только positive matches, но и collisions. Например корень `запис` не должен ошибочно перехватывать другую семантику.

---

# Owner Decision OD-IR2 — Target Architecture

После Controlled Pilot natural-language understanding должен двигаться к модели:

```text
                 USER NATURAL LANGUAGE
                          │
                          ▼
                   AYLA CONCIERGE
                          │
                    understanding
                          │
                          ▼
                     TOOL SELECTION
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
appointments.list   appointment.repeat   human.escalate
       │                  │                  │
       ▼                  ▼                  ▼
 deterministic       deterministic       deterministic
 application logic   booking logic       escalation logic
       └──────────────────┼──────────────────┘
                          ▼
                         AYLA
```

LLM отвечает за понимание контекста, выбор capability, формирование допустимых аргументов и natural-language presentation.

Application layer отвечает за authorization, identity, business rules, availability, prices, persistence, mutations, booking invariants и security.

**LLM не является источником бизнес-правды.**

---

# Owner Decision OD-IR3 — DRF-1032 строить Tool-First

DRF-1032 нельзя реализовывать как новую isolated regex business branch.

История и repeat должны стать reusable application capabilities/tools.

Концептуально:

```text
appointments.list(...)
appointments.get(...)
appointment.repeat(...)
```

Имена и schemas не копировать буквально: сначала проверить существующие abstractions и conventions.

Tool-first НЕ означает, что LLM должна вызывать capability уже на Pilot.

Переходная модель:

```text
СЕЙЧАС

deterministic detector
        ↓
thin adapter
        ↓
reusable application capability
        ↓
backend
```

После Pilot:

```text
Concierge
    ↓
tool adapter
    ↓
SAME application capability
    ↓
backend
```

Меняется caller, а бизнес-реализация остаётся.

Capability не должна быть LLM-specific function. Потенциальные callers:

```text
LLM Concierge
Button callback
Mini App
deterministic router
future channel
```

---

# Owner Decision OD-IR4 — Deterministic Gateway сохраняется

После будущей миграции deterministic layer не удаляется.

Он остаётся минимум для:

- buttons/callbacks;
- explicit state-machine transitions;
- safety gates;
- system/protocol commands;
- escalation invariants;
- строго детерминированных событий.

Исследовать fast-path для `/start`, `/help`, `/cancel` и аналогичных однозначных команд.

Целевая схема:

```text
Incoming event
      │
      ▼
Deterministic gateway
      │
      ├── callback/state transition ──► deterministic execution
      ├── safety/system event ─────────► deterministic execution
      └── natural-language request
                    │
                    ▼
                 Concierge
                    │
                    ▼
                Tool selection
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
       booking   history   escalation
```

Это post-pilot target, не pre-pilot rewrite.

---

# Owner Decision OD-IR5 — Global Concierge ↔ Capabilities

Отдельно исследовать границу global Concierge и tenant/user capability registry.

Сегодня возможна ситуация:

```text
Ayla умеет X
+
LLM понимает запрос X
+
LLM не имеет tool X
=
Ayla говорит, что не умеет X
```

Целевая модель должна дать global Concierge безопасный доступ к разрешённым capabilities, сохраняя tenant isolation, identity, authorization и capability policy.

Концептуально:

```text
Global Concierge
       │
       ▼
Capability Registry / Broker
       │
       ├── trusted user context
       ├── tenant context
       ├── authorization
       └── capability policy
               │
               ▼
            Tool
```

Не создавать новый broker/framework, пока не доказано, что существующий registry нельзя расширить.

---

# 5. Tool security invariant

LLM сообщает **что хочет пользователь**.

Trusted runtime определяет:

- кто пользователь;
- какой tenant/context;
- имеет ли он право;
- текущее состояние;
- допустимость mutation.

```text
LLM arguments
     +
trusted execution context
     ↓
application capability
     ↓
authorization + validation
     ↓
result
```

Tool schemas должны быть минимальными, typed и не принимать trusted identity fields без необходимости.

Если current subject известен runtime:

```text
appointments.list(scope="history", limit=5)
```

лучше, чем:

```text
appointments.list(
  ayla_user_id="arbitrary UUID",
  tenant_id="arbitrary tenant"
)
```

Нельзя доверять LLM ownership, identity, price, availability или authorization.

---

# 6. DRF-1032 — сохранить уже принятые product rulings

## Source of truth

Для customer-facing appointments/history источником истины является **Ayla backend**.

Bot mirror остаётся operational/internal механизмом: reminders, retries, delivery bookkeeping, degraded mechanics.

Не строить историю из stale mirror.

## History semantics

Основной пользовательский список = только состоявшиеся (`completed/attended`) визиты.

Cancelled/no-show/failed скрыты по умолчанию. Это presentation policy, не retention policy.

Предпочтительное UX-название: **«Мои визиты»**, если не конфликтует с канонизированной терминологией.

## Depth

Controlled Pilot: последние **5** состоявшихся визитов, без pagination UX.

Backend cursor не ломать.

## Repeat

«Записаться ещё» — GO.

Это **repeat intent**, а не гарантированное воспроизведение старой записи.

Перед новым booking проверить current:

- service;
- master;
- master/service relation;
- availability;
- price.

Historical price — факт прошлого, не price lock.

Если мастер/услуга/связка недоступны, вернуть graceful alternative, а не system error.

Не создавать вторую booking state machine: repeat должен входить в существующий booking flow.

## Capability result

Не кодировать presentation глубоко в application capability, если проект уже разделяет domain result и channel presentation.

Предпочтительно structured result вроде:

```text
status = master_unavailable
historical_master = ...
alternatives_available = true
```

а presentation формирует caller/channel.

Не вводить новую abstraction только ради теоретической чистоты.

---

# 7. Зависимости DRF-1035 и DRF-1037

DRF-1032 зависит от стабильной Ayla identity:

```text
DRF-1035 identity bridge
        ↓
new-user identity verified
        ↓
DRF-1032 history/repeat
```

Не создавать history-specific identity workaround.

Текущий proxy → full account linking не переносит историю. Это DRF-1037.

Для Controlled Pilot DRF-1037 **не блокирует историю**, потому что linking не используется.

Но DRF-1037 является blocker до production rollout реального proxy → full-account linking.

Production invariant:

> Identity evolution не должна разрушать видимую клиенту историю.

---

# 8. Что НЕ делать до Controlled Pilot

Не выполнять сейчас:

- full intent architecture rewrite;
- all-message LLM routing;
- новый universal agent framework;
- новый capability bus без доказанного gap;
- массовую миграцию handlers;
- удаление detector;
- удаление deterministic state machine;
- отказ от callbacks/buttons;
- LLM-owned booking machine.

Если DRF-1032 требует небольшой abstraction boundary — сделать её. Если якобы требует переписать bot platform — остановиться и эскалировать.

---

# 9. Два pre-pilot трека

## Track A — Pilot stabilization

```text
fix detector gaps
+ regression corpus
+ collision tests
+ no routing rewrite
```

## Track B — Tool-First capability preparation

```text
reusable application layer
+ thin current-router adapter
+ future Concierge-compatible boundary
```

Эти работы не должны превращаться в post-pilot migration раньше времени.

---

# 10. LLM failure modes

Post-pilot target обязан учитывать:

- LLM timeout;
- LLM unavailable;
- proxy unavailable;
- malformed tool selection;
- tool failure;
- partial outage.

При LLM outage должны продолжать работать, где возможно:

```text
deterministic system commands
buttons/callbacks
active deterministic state transitions
safety
escalation guarantees
```

Не принимать архитектуру, где outage LLM полностью выключает Ayla.

---

# 11. Observability и Pilot evidence

До Pilot полезно различать:

```text
detector_intent_matched
detector_fallback_to_llm
detector_unmatched
```

Нужно получить реальные данные:

- сколько сообщений поймал detector;
- сколько ушло в LLM fallback;
- какие формулировки промахиваются;
- какие intents дают collisions;
- в скольких fallback случаях модель поняла capability, которой у неё нет.

После tool migration понадобятся:

```text
concierge_tool_selected
tool_execution_started
tool_execution_succeeded
tool_execution_failed
tool_selection_invalid
llm_timeout
llm_fallback
```

Не строить отдельную telemetry platform, если существующей достаточно.

---

# 12. Post-Pilot migration — не Big Bang

Предпочтительное направление:

```text
Phase 1 — reusable tools/capabilities существуют
Phase 2 — Concierge получает выбранные read-only tools
Phase 3 — controlled action tools
Phase 4 — natural-language intents мигрируют с regex
Phase 5 — obsolete regex branches удаляются
Phase 6 — deterministic gateway остаётся для protocol/safety/state events
```

Исследовать, имеет ли смысл сначала выдавать read-only tools (`appointments.list`, `visit.get`), а mutation tools (`repeat`, `reschedule`, `cancel`) позже.

Не считать staged rollout обязательным без проверки safeguards.

---

# 13. Human escalation

Concierge в целевой архитектуре должен иметь capability эскалации, но не может обходить существующий invariant:

> после передачи человеку бот не продолжает говорить там, где действует escalation silence policy.

Buttons остаются first-class interaction для confirmation, выбора визита/времени, repeat, cancel, reschedule и escalation.

LLM нужен для понимания свободного языка, а не для замены каждого UI interaction.

---

# 14. Обязательное investigation перед кодом

Сначала **не писать код**.

Оркестратор должен вернуть единый отчёт.

## A. Verified Current Architecture

По реальному HEAD показать:

- detector location;
- типы правил;
- routing order;
- LLM fallback;
- tools global Concierge;
- tenant tools/capabilities;
- registry boundaries;
- buttons/callback routing;
- state machine;
- escalation;
- failure/fallback paths.

Для важных утверждений дать file/symbol references.

## B. Detector Failure Analysis

Доказать:

- почему проходят 4/13;
- почему падают остальные;
- word-order sensitivity;
- synonym/morphology gaps;
- collision risks.

## C. Pilot Stabilization Plan

Минимальный change set:

```text
known coverage fix
+ regression corpus
+ no architecture rewrite
```

## D. Tool-First Boundary for DRF-1032

Показать, где должна жить reusable history/repeat capability и как текущий router и будущий Concierge вызовут один application layer.

## E. Global Concierge Capability Gap

Показать реальную причину отсутствия нужных tools у global Concierge:

- registry;
- tenant boundary;
- authorization context;
- capability exposure.

## F. Target Architecture

Нарисовать конкретную схему:

```text
event
→ deterministic gateway
→ natural language?
→ Concierge
→ tool
→ application layer
→ backend
```

Отдельно показать callback, safety, state machine, escalation и LLM outage paths.

## G. Tool Security Model

Зафиксировать:

- trusted identity;
- tenant context;
- authorization;
- tool args;
- forbidden arbitrary subject selection;
- error semantics.

## H. Migration Plan

Чётко разделить:

```text
BEFORE PILOT
AFTER PILOT
```

## I. DRF-1032 Amendment

Подготовить amendment:

> История и repeat реализуются как reusable application capabilities/tools. Текущий deterministic intent detector до migration является только caller/adapter. Business logic не встраивается в regex branch.

Сохранить все ранее принятые product decisions истории.

## J. Follow-up Issue

Создать отдельную задачу на post-pilot intent architecture migration.

В ней должны быть:

- problem statement;
- evidence 4/13;
- target direction;
- non-goals;
- dependencies;
- pilot evidence required;
- migration stages;
- acceptance criteria.

**Задачу создать сейчас, implementation до результатов Controlled Pilot не начинать**, кроме минимальных tool boundaries, необходимых текущим capabilities.

---

# 15. Acceptance до Pilot

Detector stabilization должен доказать:

1. 13 найденных формулировок классифицируются согласно ожидаемой semantics;
2. pilot-critical intents имеют regression corpus;
3. нет очевидных новых collisions;
4. callback/button routing не изменён;
5. escalation invariants не изменены;
6. LLM fallback не ухудшен.

Цель — не «100% русского языка», а достаточная стабильность Pilot.

---

# 16. Acceptance Tool-First DRF-1032

Архитектурно правильный путь:

```text
current deterministic router
        ↓
thin adapter
        ↓
reusable history/repeat capability
        ↓
backend
```

а не:

```text
regex branch
        ↓
embedded history business logic
        ↓
backend
```

После migration должно быть возможно удалить regex adapter и добавить Concierge tool adapter **без переписывания core history/repeat logic**.

---

# 17. Owner decisions — не возвращать на согласование

```text
OD-IR1
До Controlled Pilot фундаментальный routing не менять.
Detector стабилизировать.
GO.
```

```text
OD-IR2
Post-pilot target:
Concierge understands natural language
→ selects tools
→ deterministic application layer executes.
GO.
```

```text
OD-IR3
DRF-1032 history/repeat:
Tool-First reusable capability.
Не isolated regex business branch.
GO.
```

```text
OD-IR4
Deterministic gateway сохраняется для callbacks,
state transitions, safety, system/protocol commands,
escalation invariants.
GO.
```

```text
OD-IR5
Исследовать и post-pilot устранить gap
Global Concierge ↔ tenant/user capabilities.
GO на investigation/design.
НЕ GO на pre-pilot rewrite.
```

---

# 18. Что можно эскалировать owner

Только новые реальные product/architecture decisions, например:

- изменение product semantics;
- новый registration step;
- изменение privacy boundary;
- новый внешний/платный dependency;
- изменение Controlled Pilot scope;
- необходимость big-bang migration;
- конфликт с canonical ADR.

Не возвращать вопросы уровня имени класса, DTO, regex или расположения adapter.

---

# 19. Главные invariants

> **Natural-language understanding и business execution — разные слои.**

> **LLM выбирает capability, но не является источником бизнес-правды.**

> **Trusted identity, authorization, prices, availability и mutations определяются deterministic runtime/backend.**

> **Новые capabilities не должны принадлежать одному конкретному router.**

> **LLM outage не должен уничтожать deterministic safety, callbacks, active state transitions и escalation guarantees.**

> **Если Ayla технически обладает разрешённой пользователю capability, global Concierge в целевой архитектуре должен иметь безопасный путь к ней.**

---

# 20. Итоговое поручение

Сейчас:

```text
1. Не переписывать intent architecture.
2. Исследовать и доказать current state.
3. Подготовить минимальный detector stabilization до Pilot.
4. Скорректировать DRF-1032 на Tool-First implementation.
5. Создать отдельную post-pilot architectural issue.
6. Не начинать её implementation до результатов Controlled Pilot.
```

Для DRF-1032 сейчас:

```text
detector
   ↓
thin adapter
   ↓
reusable history/repeat capability
   ↓
backend
```

В будущем:

```text
Concierge
   ↓
tool adapter
   ↓
SAME history/repeat capability
   ↓
backend
```

Мы сознательно сохраняем текущий routing на время Controlled Pilot, но **перестаём строить новые бизнес-возможности внутри архитектуры, которую собираемся выводить из эксплуатации**.

Начни с investigation по текущему HEAD. До доказательства реальной routing/tool boundary и минимального pre-pilot change set код не писать.
