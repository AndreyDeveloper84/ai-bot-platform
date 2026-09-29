# DRF-1035 — промпт для оркестратора-архитектора: отдельное backend-окно

Ты работаешь как **оркестратор-архитектор Ayla**. По DRF-1035 завершено расследование, owner verdict принят, bot-side уже ушёл в реализацию отдельным окном. Теперь требуется организовать **backend-часть DRF-1035 как отдельное рабочее окно**.

Это сознательное решение владельца. Backend-часть нельзя незаметно присоединять к уже работающему bot-side окну.

---

# 1. Owner decision

## Backend implementation — GO

Разрешаю реализацию backend-части DRF-1035.

Но:

> **backend должен выполняться отдельным окном/агентом от bot-side реализации.**

Причина — не организационная формальность.

Backend является отдельной deployment boundary:

```text
DRF-1035
   │
   ├── Window A — Bot platform
   │      ├── apps/identity
   │      ├── ensure_ayla_link()
   │      ├── BotUser persistence
   │      └── integration with active capabilities
   │
   └── Window B — Ayla backend
          ├── current/resolved identity endpoint
          ├── existing auth + resolver integration
          ├── backend tests
          ├── separate PR
          ├── separate deploy
          └── separate rollback
```

У backend:

- свой репозиторий;
- свой PR;
- свой deployment process;
- своя точка отката;
- это первая кодовая мутация backend-стека в рамках текущей работы — ранее его трогали только данными.

Поэтому bot-side и backend-side должны иметь **один согласованный контракт**, но независимые implementation/review/deploy boundaries.

---

# 2. Что было доказано расследованием

Не возвращайся к исходной гипотезе, будто проблема заключается в отсутствии identity architecture.

Identity architecture уже существует.

Настоящий root cause:

```text
BotUser.ayla_user_id
```

в штатном production flow фактически никто не заполняет.

Resolver умеет записывать связь и это покрыто тестами, но channel handlers вызывают его без соответствующего аргумента.

То есть состояние:

```text
BotUser.ayla_user_id = NULL
```

является не случайным edge case, а следствием отсутствующего bridge между уже существующими частями системы.

Работающий owner/test account оказался вручную provisioned.

Его `ayla_user_id` указывает на его собственную proxy identity:

```text
is_proxy = true
linked_user = null
```

Это не доказательство существующего автоматического linking flow.

---

# 3. Дополнительно доказана глобальность identity

Ранее owner специально потребовал не принимать предположение о global-vs-tenant identity без доказательства.

Расследование это доказательство получило.

Подтверждены несколько независимых признаков, включая решающий:

```text
external identity = bot:max:<channel_user_id>
```

не содержит tenant component.

Кроме того:

- username/subject identity глобально уникальна;
- backend model декларирует клиента как multiprovider по умолчанию;
- у клиента отсутствует понятие обязательного «главного tenant»;
- backend evidence подтверждает глобальный характер subject identity.

Следовательно, tenant-scoped identity здесь не просто маловероятна — она несовместима с текущей моделью идентичности.

Это позволяет bot-side сохранять один resolved Ayla subject для строк одного channel user в применимых tenant contexts согласно уже принятому bot-side verdict.

Backend-окну не нужно повторно проектировать tenant identity.

Но оно должно проверить эти факты в текущем HEAD перед изменением кода и зафиксировать ссылки на модели/контракты в отчёте.

---

# 4. Важная поправка по consent

Предыдущий отчёт содержал неверный вывод:

> пользователь с `ayla_user_id = NULL` не может отозвать consent и получает ложный success.

Этот вывод **отозван**.

Дополнительная проверка call sites доказала:

- живой revoke path работает через локальную identity;
- он намеренно не зависит от Ayla linking;
- в коде есть явный комментарий об этом;
- соответствующая проблема была закрыта ранее;
- опасная функция, найденная в таблице расследования, сейчас не является live path.

Поэтому:

> **не создавать consent-работу в DRF-1035 ради исправления несуществующего production defect.**

Verified no-op является корректным результатом расследования.

Не изобретать изменения ради формального выполнения старого брифа.

---

# 5. Цель backend-окна

Нужно добавить **минимальный способ для bot platform узнать Ayla identity уже аутентифицированного и резолвнутого external subject**.

Bot platform после этого сможет выполнить:

```text
MAX user
   ↓
authenticated external identity
   ↓
backend
   ↓
existing resolver
   ↓
Ayla proxy/current subject
   ↓
GET current identity
   ↓
ayla_user_id
   ↓
BotUser.ayla_user_id persisted
```

Backend не должен превращаться в новый provisioning service.

Он должен лишь безопасно открыть уже существующий результат identity resolution текущему доверенному bot caller.

---

# 6. Предпочтительный контракт

Исследованием предложен минимальный endpoint:

```http
GET /api/v1/internal/me/
```

под существующим permission/auth path:

```text
IsBotServiceWithVerifiedClient
```

Концептуальный ответ:

```json
{
  "ayla_user_id": "<UUID>",
  "is_proxy": true
}
```

Это **предпочтительное направление**, а не разрешение слепо вставить endpoint без проверки conventions репозитория.

Перед реализацией backend-окно обязано подтвердить:

1. текущий URL namespace;
2. conventions internal API;
3. существующий authentication pipeline;
4. где именно происходит `resolve_external_user`;
5. что `IsBotServiceWithVerifiedClient` действительно гарантирует verified current external subject;
6. что endpoint не требует принимать subject/client identifiers от caller;
7. что returned UUID соответствует тому же subject, который backend получил из authentication context.

Если существующая архитектура требует другого URL/name — разрешается адаптировать имя, но **семантика должна остаться current authenticated subject**, а не arbitrary lookup API.

---

# 7. Почему GET /me, а не POST /resolve-user

Предпочтительная модель:

```text
caller authenticates as external subject A
                ↓
backend resolves subject A
                ↓
GET /me
                ↓
backend returns Ayla identity A
```

Не создавать без необходимости:

```http
POST /resolve-user
```

с телом:

```json
{
  "external_user_id": "bot:max:OTHER_USER"
}
```

или:

```json
{
  "client_id": "OTHER_UUID"
}
```

Причина принципиальная.

Если backend уже знает current external subject из verified authentication context, caller не должен иметь возможность выбирать другого субъекта.

Endpoint должен отвечать на вопрос:

> «Какой Ayla subject соответствует **мне**, уже аутентифицированному external caller?»

а не:

> «Дай мне Ayla subject для произвольного external ID, который я назову».

Это уменьшает поверхность subject substitution и сохраняет текущую security boundary.

---

# 8. Booking security contract не менять

Существующий booking endpoint требует `client_id` в request body и сопоставляет его с authenticated/resolved subject.

Этот механизм **не удалять и не ослаблять**.

DRF-1035 должен сделать так, чтобы нормальный запрос наконец удовлетворял этому контракту:

```text
authenticated subject = A
body.client_id = A
→ allow
```

Negative case должен сохраниться:

```text
authenticated subject = A
body.client_id = B
→ reject
```

Нельзя «починить» DRF-1035 удалением `client_id` check.

Именно после identity bridge существующая защита должна начать работать как задумано.

---

# 9. Scope backend-окна

Backend-окно должно быть максимально узким.

## Входит

1. Проверка текущего auth/resolver path.
2. Проверка существующего `resolve_external_user`.
3. Реализация current/resolved subject endpoint.
4. Минимальная serializer/response schema, если требуется архитектурой проекта.
5. Permission wiring.
6. Unit tests.
7. Integration/API tests.
8. Negative authorization tests.
9. Проверка idempotency существующего resolver path.
10. Документирование API contract для bot-side.
11. Подготовка отдельного PR.
12. Подготовка deployment plan.
13. Подготовка rollback plan.
14. Post-deploy smoke verification endpoint.

## Не входит

- изменение booking business logic;
- удаление booking `client_id` cross-check;
- новый identity subsystem;
- новый auth subsystem;
- телефон;
- OTP;
- full registration;
- proxy → real account merge;
- перенос истории;
- Ayla-mediated communication;
- массовый рефакторинг resolver;
- broad eventbus refactor;
- unrelated consent changes;
- исправление DRF-1036 в том же PR.

Если обнаружится реальный blocker, без которого endpoint невозможно безопасно реализовать, остановись и эскалируй его отдельно. Не расширяй scope молча.

---

# 10. DRF-1036 — найденный security defect

Расследование обнаружило отдельную проблему безопасности.

Есть backend surfaces, где знание Ayla UUID в сочетании с internal Bearer потенциально позволяет обратиться к данным другого subject.

В расследовании указаны как минимум:

```text
/internal/users/{id}/
/personal-context/
/personal-data/export/
DELETE /personal-data/
```

Суть риска:

```text
leaked/abused internal token
        +
known victim UUID
        ↓
cross-subject access
```

Новый current-user endpoint не создаёт первопричину.

Дефект латентно существует уже сейчас.

Однако новый endpoint потенциально делает сопоставление:

```text
enumerable external ID
→ Ayla UUID
```

дешевле.

Поэтому DRF-1036 является реальной отдельной задачей.

### Owner ruling

Для закрытого Controlled Pilot:

```text
DRF-1036 ≠ blocker
```

при текущем ограниченном pilot/staging perimeter.

Для production release:

```text
DRF-1036 = BLOCKER
```

Но:

> **не исправлять DRF-1036 внутри backend PR DRF-1035.**

Причина — это другая security surface, другой blast radius и другой набор тестов.

Нам нужны независимые:

```text
PR
review
verification
rollback
```

для identity bridge и authorization hardening.

---

# 11. DRF-1037 — proxy → real account history

Ещё одно подтверждённое ограничение:

```text
bind_external_identity
```

не переносит исторические данные proxy subject в полноценный account.

Binding меняет future resolution, но старые данные могут остаться на proxy subject.

Это касается потенциально:

- bookings;
- context;
- logs;
- export/delete scope;
- других subject-owned records.

### Owner ruling

Для Controlled Pilot:

```text
ACCEPTED KNOWN LIMITATION
```

поскольку full account binding в пилотном сценарии не используется.

До production использования linking:

```text
DRF-1037 must be resolved
```

Но:

> **не реализовывать history merge в DRF-1035 backend window.**

---

# 12. Телефон и регистрация

DRF-1035 не должен добавлять:

- запрос телефона;
- OTP;
- регистрацию;
- раскрытие контакта мастеру.

Продуктовый принцип Ayla:

```text
Client
   ↓
 Ayla
   ↓
Salon / Master
```

Для первого booking MAX external identity является минимально достаточной identity, если Ayla может продолжать общение через MAX.

Коммуникационный слой является отдельной capability — DRF-1039.

---

# 13. Backend endpoint должен быть минимальным

Не возвращай из `/me/` полноценный customer object просто потому, что он доступен.

Нужен минимальный contract.

Предпочтительно:

```json
{
  "ayla_user_id": "...",
  "is_proxy": true
}
```

Перед добавлением любых дополнительных полей ответь:

> Нужно ли это bot-side для DRF-1035?

Если нет — не добавляй.

Особенно не возвращать без необходимости:

- phone;
- email;
- profile details;
- tenant data;
- consent state;
- personal context;
- booking history.

Это identity endpoint, а не customer-profile endpoint.

---

# 14. Auth failure semantics

Явно определить и протестировать минимум:

```text
no bearer
→ reject

invalid bearer
→ reject

missing external identity
→ reject or explicit contract error

invalid external identity
→ reject

valid bot service + valid external subject
→ resolve current subject
→ return identity
```

Не возвращать чужой subject и не делать fallback на arbitrary/default client.

Ошибки должны быть диагностируемыми в логах, но не раскрывать лишнюю внутреннюю информацию caller/user.

---

# 15. Resolver semantics

Не переписывать resolver без необходимости.

Нужно доказать текущую семантику:

```text
same external identity
→ same canonical Ayla proxy/current subject
```

Проверить:

- repeated requests;
- concurrent requests;
- uniqueness constraint;
- race handling;
- proxy creation;
- existing subject resolution.

Если idempotency уже обеспечена backend model/constraint — использовать её.

Если обнаружится реальная race, которая позволяет создать duplicates, не маскировать её. Остановиться и сообщить как blocker/отдельный defect.

---

# 16. Обязательные backend tests

Минимальная матрица.

### Authentication

```text
1. no auth → rejected
2. invalid bot bearer → rejected
3. missing external subject → rejected
4. valid verified bot caller → accepted
```

### Identity resolution

```text
5. existing proxy → same UUID returned
6. first valid external subject → proxy/current subject resolved
7. repeated GET → same UUID
8. is_proxy accurately reflects model
```

### Isolation

```text
9. caller A cannot ask for B through request parameters
10. endpoint has no arbitrary subject selector
```

### Booking contract compatibility

Не обязательно создавать booking внутри endpoint tests, если это другой test layer, но должен быть integration proof:

```text
resolved subject A
→ returned ayla_user_id A
→ booking body client_id A
→ existing cross-check accepts
```

И negative proof:

```text
resolved subject A
→ booking body client_id B
→ rejected
```

### Regression

```text
existing internal API behaviour unchanged
existing resolver tests remain green
existing booking tests remain green
```

---

# 17. Deployment sequence

Это критично.

Новый bot-side будет зависеть от endpoint, которого сейчас нет.

Поэтому deployment order:

```text
1. Backend PR merged
        ↓
2. Backend deployed
        ↓
3. /me endpoint smoke-tested
        ↓
4. Existing bot verified unchanged
        ↓
5. Bot-side PR merged/deployed
        ↓
6. New MAX user E2E
```

Не делать наоборот.

Нельзя создавать окно:

```text
new bot deployed
        ↓
calls /me
        ↓
404
```

Backend изменение должно быть backward-compatible с текущим bot version.

---

# 18. Backend rollback

Поскольку backend-кодовая поверхность для нас новая, rollback должен быть доказан до GO на deploy.

Backend-окно обязано зафиксировать:

```text
base SHA
change SHA
deployment artifact/version
rollback target
rollback command/process
```

Rollback должен возвращать backend в предыдущую версию без миграционного хвоста.

Поскольку по текущему плану:

```text
DB migrations = none
```

rollback должен быть простым code rollback.

Если в ходе реализации внезапно появляется migration — остановись.

Это изменение scope и требует отдельного review.

---

# 19. Bot/backend contract handoff

До merge backend PR создай точный contract handoff для bot-side окна.

В нём должны быть:

```text
method
path
required headers
auth assumptions
success status
success JSON
error statuses
timeout expectations
retry safety
idempotency statement
```

Например концептуально:

```text
GET /api/v1/internal/me/

Auth:
  existing internal bearer
  existing verified external subject header/context

200:
{
  "ayla_user_id": "uuid",
  "is_proxy": true
}

Caller does not supply:
  client_id
  external_user_id
```

Но значения должны быть взяты из реального реализованного контракта, а не скопированы из этого примера.

---

# 20. Observability

Не строить новую telemetry систему.

Но endpoint должен позволять диагностировать Controlled Pilot.

Минимально нужны структурированные события/логи, позволяющие различить:

```text
identity_me_requested
identity_me_resolved_existing
identity_me_resolved_proxy
identity_me_failed_auth
identity_me_failed_resolution
```

Не логировать лишние ПДн.

Если external subject нужен для диагностики, следовать существующей logging/privacy policy проекта.

---

# 21. Что считать P0 для backend-окна

До Controlled Pilot обязательно:

```text
P0-1 endpoint implemented
P0-2 auth verified
P0-3 correct current subject returned
P0-4 no arbitrary subject selection
P0-5 repeated call stable
P0-6 booking cross-check compatibility proven
P0-7 tests green
P0-8 backend deploy proven
P0-9 rollback documented
P0-10 post-deploy smoke successful
```

---

# 22. Что НЕ блокирует этот backend PR

Не задерживать DRF-1035 backend implementation ради:

```text
DRF-1036 authorization hardening
DRF-1037 history reconciliation
DRF-1039 mediated communication
phone collection
OTP
full account registration
general identity refactor
unused consent cleanup
```

Они должны остаться отдельными задачами с явными dependencies.

---

# 23. Что требуется от оркестратора прямо сейчас

Создай **отдельное backend implementation window**.

Не отдавай backend scope уже работающему bot-side окну.

Перед тем как разрешить агенту писать код, потребуй короткий preflight:

## A. Repository / deployment boundary

Указать:

- repository;
- branch/base;
- current HEAD;
- deployment mechanism;
- staging target;
- rollback mechanism.

## B. Verified code path

Показать конкретные:

- auth class;
- resolver;
- external subject extraction;
- current proxy model;
- URL routing location;
- closest analogous internal endpoint.

## C. Contract

Зафиксировать:

```text
method
path
auth
request
response
errors
```

## D. Scope

Подтвердить:

```text
no migration
no booking rewrite
no DRF-1036 fix
no history merge
```

Только после этого — implementation.

---

# 24. После реализации backend

Окно должно вернуть не просто «готово».

Требуется отчёт:

```text
1. What changed
2. Files changed
3. Why each change was necessary
4. Final API contract
5. Security properties preserved
6. Tests run
7. Test results
8. Migration status
9. PR/commit
10. Deploy procedure
11. Rollback procedure
12. Smoke-test evidence
13. Known limitations
14. Bot-side handoff
```

Если deployment ещё не разрешён, разделить:

```text
CODE READY
DEPLOYMENT PENDING OWNER GO
```

Не деплоить молча.

---

# 25. E2E gate после двух окон

DRF-1035 не закрывается после backend unit tests и не закрывается после bot-side unit tests.

Финальный gate — совместный E2E:

```text
NEW MAX USER
BotUser.ayla_user_id = NULL
        ↓
first Ayla-dependent action
        ↓
bot ensure_ayla_link()
        ↓
GET backend current subject
        ↓
existing resolver returns/creates proxy
        ↓
backend returns ayla_user_id
        ↓
bot persists ayla_user_id
        ↓
booking uses same client_id
        ↓
backend cross-check succeeds
        ↓
appointment created
```

После этого проверить повтор:

```text
same MAX user
        ↓
BotUser already linked
        ↓
no unnecessary identity lookup
        ↓
same Ayla subject
        ↓
second operation succeeds
```

И минимум один downstream event:

```text
backend event
        ↓
ayla_user_id
        ↓
BotUser lookup
        ↓
correct MAX user
```

---

# 26. Финальный owner ruling

```text
BACKEND DRF-1035: GO
```

```text
EXECUTION: SEPARATE WINDOW
```

```text
CONTRACT:
current authenticated/resolved subject endpoint
```

```text
PREFERRED SHAPE:
GET /api/v1/internal/me/
→ {ayla_user_id, is_proxy}
```

с финальным URL согласно conventions реального backend.

```text
DEPLOY ORDER:
backend first
→ smoke
→ bot second
→ E2E
```

```text
DRF-1036:
separate production blocker
not part of this PR
```

```text
DRF-1037:
known Controlled Pilot limitation
separate pre-production/full-linking work
```

```text
PHONE / OTP / REGISTRATION:
out of scope
```

```text
CONSENT FALSE-SUCCESS CLAIM:
withdrawn; do not invent a fix
```

---

# 27. Главный принцип

Здесь не нужно строить новую систему.

Нужно соединить две уже существующие части:

```text
backend already knows who the external user is
+
bot needs the corresponding Ayla UUID
=
small authenticated current-subject contract
```

Сделай **минимальное изменение, которое восстанавливает отсутствующий identity bridge**, сохраняя существующие security checks и deployment boundaries.

Начни с создания отдельного backend-окна и preflight-доказательства. Не разрешай агенту расширять scope «заодно».
