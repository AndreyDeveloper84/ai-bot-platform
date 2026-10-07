# Goal Plan Implementation Handoff v1

Статус: готово к декомпозиции backend/orchestrator

## Что реализовать

Реализовать универсальный контур пользовательских целей:

- снижение массы;
- набор массы;
- удержание массы;
- аналогичные измеримые цели, если они поддержаны каталогом единиц.

Не зашивать пример `−5 кг за 30 дней` в код, шаблоны или тестовую бизнес-логику.

## Backend: beautygo_backend

### 1. RequestedTarget

Добавить сущность или расширение goal-domain с полями:

- direction;
- amount;
- unit;
- period_days;
- mass_type;
- source;
- status;
- created_at;
- confirmed_at.

Исходный пользовательский запрос должен сохраняться отдельно от рассчитанного результата.

### 2. Target validation service

Добавить чистый детерминированный сервис:

`validate_requested_target(context, requested_target) -> decision`

Сервис не вызывает LLM. Он проверяет:

- достаточность входных данных;
- consent;
- возраст;
- health flags;
- допустимость величины и срока по утверждённым правилам;
- необходимость уточнения или handoff.

Результат: `ACCEPT`, `CLARIFY`, `CAUTION`, `BLOCKED`, `HANDOFF`.

### 3. Plan Lite

Изменить создание плана:

- ручной план может создаваться без `goal_id`;
- шаблонное предложение требует активной цели;
- план хранит направление цели и снимок исходного запроса, если он был;
- действия остаются закрытым каталогом;
- расчётные показатели не являются доказательством достижения цели.

### 4. Revision history

Добавить append-only историю изменений:

- revision_number;
- source: user/template/engine;
- changed_fields;
- reason;
- decision_id;
- effective_at.

Изменение цели не должно переписывать прошлую ревизию плана.

### 5. API

Добавить внутренние endpoint-операции:

- create/read requested target;
- validate target;
- create manual plan without goal;
- create plan revision;
- read current plan plus revision metadata.

## Orchestrator: ai-bot-platform

### 1. Intent parsing

Извлекать из сообщения только явно названные пользователем поля:

- direction;
- amount;
- unit;
- period;
- mass_type.

Если поле не названо, возвращать `null`, а не угадывать.

### 2. Routing

Запросы `lose`, `gain`, `maintain` направлять в goal-plan flow, а не смешивать с обычным food interpretation flow.

### 3. Decision policy

Backend-решение является авторитетным. Orchestrator:

- запрашивает недостающие данные при `CLARIFY`;
- показывает осторожное предупреждение при `CAUTION`;
- не предлагает nutrition plan при `BLOCKED`;
- предлагает handoff при `HANDOFF`;
- использует LLM только для формулировки ответа.

### 4. UI/API payload

В карточке цели показывать:

- направление;
- пользовательскую величину;
- пользовательский срок;
- тип массы, если указан;
- статус проверки;
- что является фактом, а что только пожеланием.

Не показывать «цель достигнута» на основании одного соблюдения плана.

## Тестовый минимум

Добавить replay/contract tests:

1. lose с amount и period;
2. lose без period;
3. gain body_weight;
4. gain muscle_mass без подтверждённых измерений;
5. maintain без amount;
6. missing inputs;
7. minor;
8. pregnancy/lactation;
9. eating-disorder signal;
10. manual plan without goal;
11. template plan with goal;
12. revision after target change.

## Порядок реализации

1. Backend schema/API и validation service.
2. Backend tests.
3. Orchestrator routing and client methods.
4. Replay tests.
5. UI payload and wording.
6. Только после зелёных тестов — включение feature flag.

## Критерий готовности

Система одинаково обрабатывает lose/gain/maintain, не подменяет пользовательские параметры, не обещает результат и не позволяет LLM принимать safety-решения.
