# Prompt for Ayla Orchestrator-Architect — DRF-1035 implementation ruling

Ты работаешь как **оркестратор-архитектор Ayla**. Расследование DRF-1035 завершено. Факты ниже считаются исходной базой для решения, но перед изменениями в коде ты обязан сверить критичные детали по репозиториям и не расширять scope без необходимости.

Твоя задача: принять owner rulings ниже как направление, подготовить минимальный implementation plan, проверить границы безопасности и провести реализацию DRF-1035 без превращения локального исправления в большой рефакторинг.

---

## 1. Подтверждённый root cause

Проблема не в том, что некоторые новые пользователи MAX «иногда не связаны».

В production-коде фактически отсутствует рабочий writer для:

```text
BotUser.ayla_user_id
```

Механизм заполнения поля уже существует в resolver и покрыт тестами, но оба MAX-обработчика и Telegram вызывают resolver без аргумента, который должен сохранять Ayla identity в BotUser.

Следствие:

```text
BotUser.ayla_user_id
```

не является «редко пустым полем».

Он системно остаётся `NULL`, если кто-то не заполнил его вручную.

Текущий работающий owner/test account — следствие ручного provisioning, а не штатного lifecycle.

---

## 2. Фактический blast radius

Проверено 16 потребителей `BotUser.ayla_user_id`.

Пустое поле ломает не только booking.

Подтверждённые последствия включают:

- создание записи;
- persistent memory между сессиями;
- глобальное согласие на memory;
- отзыв согласия;
- 152-ФЗ export;
- personal-data lifecycle;
- Mini App сценарии;
- пять inbound eventbus consumers;
- напоминания;
- события платежей;
- события отзывов.

Особенно опасный кейс:

```text
revoke consent
    ↓
ayla_user_id == NULL
    ↓
операция фактически ничего не отзывает
    ↓
возвращается 0
    ↓
выглядит как успешная
```

Такое состояние нельзя оставлять в Controlled Pilot, если соответствующий пользователь уже начал пользоваться persistent Ayla capabilities.

На пилоте обнаружено:

```text
7 / 12 BotUser rows
```

без `ayla_user_id`.

---

# 3. Минимальное техническое решение

Исследование предлагает следующий минимальный contract.

Backend:

```http
GET /api/v1/internal/me/
```

под существующим:

```text
IsBotServiceWithVerifiedClient
```

Ответ концептуально:

```json
{
  "ayla_user_id": "<uuid>",
  "is_proxy": true
}
```

Ключевые свойства:

- request body отсутствует;
- caller не передаёт произвольный `client_id`;
- backend возвращает identity уже аутентифицированного/resolved external subject;
- существующая проверка `client_id` в booking body не удаляется;
- после исправления она начинает сходиться;
- миграции БД не требуются;
- booking contract не переписывается.

На стороне bot platform должна появиться переиспользуемая identity capability, например:

```text
apps/identity/ensure_ayla_link()
```

Точное имя и расположение сверить с текущей архитектурой.

Она должна:

1. проверить существующий `BotUser.ayla_user_id`;
2. если он уже есть — вернуть его без сетевого вызова;
3. если его нет — обратиться к backend identity endpoint;
4. получить resolved `ayla_user_id`;
5. персистентно сохранить связь;
6. вернуть Ayla identity вызывающему capability;
7. быть идемпотентной;
8. корректно работать при retry;
9. не создавать duplicate proxy identities.

---

# 4. Owner ruling J-O1 — GO

**GO на реализацию решения раздела E расследования.**

Canonical direction:

```text
authenticated external user
        ↓
backend resolve
        ↓
GET current/resolved Ayla subject
        ↓
BotUser.ayla_user_id
        ↓
business capability
```

Не создавать:

- новый identity subsystem;
- отдельную регистрацию;
- обязательный телефон;
- обязательный OTP;
- booking-specific user creation;
- параллельный proxy lifecycle.

DRF-1035 должен использовать уже существующую identity model.

---

# 5. Owner ruling J-O2 — известное ограничение proxy → real account

Подтверждено, что текущий:

```text
bind_external_identity
```

не выполняет требование:

> история proxy-клиента сохраняется и становится историей полноценного аккаунта.

Фактическое поведение:

```text
proxy identity
      ↓
bind external identity
      ↓
future resolution points to real account
```

но существующие:

- bookings;
- context;
- logs;
- и другие данные

физически остаются привязаны к proxy subject.

В результате они могут стать невидимыми через обычный resolution path и выпадать из корректной области export/delete.

### Решение

Для **Controlled Pilot** принять это как **known limitation**.

Причины:

- полноценный account binding на пилоте не используется;
- риск для текущего pilot flow практически отсутствует;
- исправление merge semantics значительно шире DRF-1035.

Но это не принимается как нормальная production semantics.

DRF-1037 / соответствующая follow-up задача должна определить canonical:

```text
proxy subject
      ↓
verified/full account
      ↓
history/data ownership reconciliation
```

До production использования account linking этот вопрос должен быть закрыт.

Не пытаться исправить proxy merge внутри DRF-1035.

---

# 6. Owner ruling J-O3 — спор с формулировкой «при первом контакте»

Не выбирать booking-only.

Но также **не выполнять identity resolution буквально при любом первом сообщении пользователя боту**.

Формулировка:

```text
first contact with bot
```

слишком широкая.

Например:

```text
Пользователь:
«Привет»

→ создание persistent proxy identity
```

необязательно и нарушает принцип минимально необходимой идентичности.

### Канонический invariant

Выполнять:

```text
ensure_ayla_link()
```

**при первом Ayla-dependent действии**, которому действительно требуется persistent Ayla subject.

То есть:

```text
первый контакт
      │
      ├── stateless/help/info action
      │       └── identity resolution НЕ требуется
      │
      └── persistent / identity-dependent action
              ↓
       ensure_ayla_link()
```

Identity resolution обязателен до операции, если capability:

- создаёт booking;
- пишет/читает persistent memory;
- создаёт глобальное consent state;
- отзывает consent;
- выполняет personal-data export/delete;
- требует стабильного customer subject;
- создаёт событие, которое позже должно маршрутизироваться обратно этому клиенту.

Это сохраняет два принципа одновременно:

1. **никакого booking-only workaround**;
2. **никакого преждевременного создания persistent identity для каждого случайного диалога**.

---

# 7. P0 integration scope

В текущем implementation window подключить `ensure_ayla_link()` минимум к:

```text
1. Booking
2. Persistent memory
3. Consent / revoke consent
4. Personal-data operations, если они выполняются через bot flow
```

Особенно важно закрыть ложный-success сценарий отзыва consent.

Если memory и consent используют общий верхнеуровневый middleware/service boundary, предпочесть один вызов там вместо размножения ad-hoc checks.

Не вставлять одинаковую реализацию в каждый handler.

---

# 8. Eventbus consumers

Пять inbound consumers зависят от:

```text
BotUser.ayla_user_id
```

После внедрения identity resolution необходимо проверить их поведение.

Не требуется автоматически переписывать все consumers в рамках DRF-1035, если они уже корректно работают после появления заполненного поля.

Нужно доказать:

```text
resolved user
    ↓
BotUser.ayla_user_id persisted
    ↓
incoming event by ayla_user_id
    ↓
correct BotUser found
    ↓
notification delivered
```

Проверить минимум:

- reminders;
- payments;
- reviews;
- остальные два существующих consumer type.

---

# 9. Persistence semantics

Расследование предлагает записывать resolved identity во все строки одной пары:

```text
(channel, channel_user_id)
```

по tenant contexts.

Перед реализацией **обязательно доказать scope Ayla `client_id`**.

Проверить:

```text
Ayla client_id = global subject?
```

или:

```text
Ayla client_id = tenant-scoped subject?
```

Если identity глобальна для клиента, fan-out по tenant BotUser rows допустим и желателен.

Если identity tenant-scoped, массовая запись одного UUID во все tenant rows является ошибкой.

Не принимать это предположение без проверки модели backend и resolver contract.

---

# 10. Security finding — DRF-1036

Обнаружен отдельный security defect.

Существуют поверхности, где знание Ayla UUID в сочетании с internal Bearer может позволить операции над чужим субъектом.

Указаны как минимум:

```text
/internal/users/{id}/
/personal-context/
/personal-data/export/
DELETE /personal-data/
```

Новый `/internal/me/` не создаёт первопричину, но может удешевить эксплуатацию, поскольку сопоставляет enumerable external subject с Ayla UUID.

### Решение

Не смешивать исправление DRF-1036 с DRF-1035.

Для закрытого Controlled Pilot:

```text
DRF-1036 != pilot blocker
```

при условии ограниченного staging/pilot environment.

Но:

```text
DRF-1036 = production release blocker
```

до выхода этой поверхности в production.

Нельзя закрывать DRF-1035 утверждением, что security вопрос отсутствует.

---

# 11. Booking security contract

Не удалять и не ослаблять существующий cross-check:

```text
authenticated/resolved subject
vs
client_id in booking body
```

После DRF-1035 он должен начать выполняться корректно.

Проверить negative test:

```text
resolved subject = A
body.client_id = B
→ request rejected
```

И positive test:

```text
resolved subject = A
body.client_id = A
→ booking allowed
```

---

# 12. Телефон

Телефон **не добавлять в DRF-1035**.

Продуктовое направление Ayla:

```text
Client
   ↓
 Ayla
   ↓
Salon / Master
```

а не прямое раскрытие контактного канала.

Для первой записи MAX external identity является достаточной identity, если Ayla способна продолжать коммуникацию с пользователем через MAX.

Ayla-mediated communication вынесена отдельно в DRF-1039.

Не добавлять:

- обязательный телефон;
- OTP;
- регистрацию;
- раскрытие номера мастеру

как побочный эффект исправления identity.

---

# 13. Controlled Pilot workaround

Разрешён ограниченный manual provisioning тестовых аккаунтов, если он нужен для продолжения E2E до завершения реализации.

### GO на pilot mutation только при условиях:

- только заранее выбранные тестовые аккаунты;
- не выполнять bulk backfill всех 7/12 записей;
- явно логировать, какие BotUser были изменены;
- сохранить before/after;
- не выдавать workaround за production mechanism;
- после реализации DRF-1035 перепроверить эти аккаунты штатным flow.

---

# 14. Acceptance criteria DRF-1035

DRF-1035 нельзя считать закрытым только по unit tests.

Нужен реальный E2E с новым MAX user.

## AC-1 — New MAX user

Начальное состояние:

```text
BotUser.ayla_user_id = NULL
```

После первого identity-dependent action:

```text
ensure_ayla_link()
→ backend resolved identity
→ BotUser.ayla_user_id persisted
```

---

## AC-2 — Booking

Новый пользователь проходит:

```text
new MAX user
→ identity resolution
→ booking
→ create appointment
→ success
```

без:

```text
ayla_client_id_missing
```

---

## AC-3 — Existing linked user

Если поле заполнено:

```text
BotUser.ayla_user_id != NULL
```

лишний resolve не выполняется.

---

## AC-4 — Idempotency

Повторные вызовы не создают:

- duplicate proxy;
- duplicate bindings;
- inconsistent BotUser states.

---

## AC-5 — Parallel calls

Два параллельных identity-dependent действия для одного пользователя приводят к одному canonical Ayla subject.

---

## AC-6 — Memory

После первого identity-dependent interaction persistent memory использует тот же Ayla subject.

Проверить минимум:

```text
session 1 → memory write
session 2 → same user → memory available
```

---

## AC-7 — Consent create

Global/persistent consent создаётся для resolved subject.

---

## AC-8 — Consent revoke

Пользователь с ранее созданным consent может реально его отозвать.

Нельзя считать результат:

```text
0 affected
```

автоматическим success без проверки semantics.

---

## AC-9 — Personal data

Если export/delete доступны через данный flow, они используют тот же resolved subject.

---

## AC-10 — Eventbus

Хотя бы один реальный/интеграционный event проходит:

```text
backend event
→ ayla_user_id
→ BotUser
→ correct channel user
```

---

## AC-11 — No registration wall

Новый MAX user не обязан:

- вводить телефон;
- проходить OTP;
- создавать full account

для первого booking.

---

## AC-12 — Security cross-check preserved

Чужой `client_id` по-прежнему отвергается.

---

# 15. Tests

Обязательная test matrix:

```text
1. new MAX user, no ayla_user_id
2. existing linked BotUser
3. resolver returns existing proxy
4. resolver creates new proxy
5. repeated ensure_ayla_link
6. parallel ensure_ayla_link
7. backend unavailable
8. booking after identity resolution
9. body client_id mismatch
10. memory after identity resolution
11. consent grant
12. consent revoke
13. eventbus lookup
14. multi-tenant BotUser rows
15. no duplicate proxy creation
```

Если personal-data flow входит в bot surface:

```text
16. export
17. delete
```

---

# 16. Observability

Добавить минимально необходимые события/логи.

Например:

```text
identity_link_requested
identity_link_cache_hit
identity_link_resolved
identity_link_persisted
identity_link_failed
identity_link_race
booking_after_identity_link_success
booking_after_identity_link_failed
```

Не строить отдельную telemetry platform.

На пилоте мы должны суметь ответить:

- сколько пользователей потребовали resolution;
- сколько связались успешно;
- сколько упало;
- появились ли duplicates;
- сколько дошло до booking;
- работают ли последующие eventbus notifications.

---

# 17. Related candidates

Расследование создало:

```text
DRF-1036 — subject authorization / UUID security
DRF-1037 — proxy → real account history/data reconciliation
DRF-1038 — backend 152-ФЗ gate
DRF-1039 — Ayla-mediated communication
DRF-1040 — follow-up minor issue
DRF-1041 — follow-up minor issue
```

Проверь их описания и зависимости.

Не поглощать их автоматически в DRF-1035.

DRF-1035 должен оставаться минимальным identity-foundation fix.

---

# 18. Что требуется от оркестратора сейчас

Перед кодом дай короткий pre-implementation verdict:

## A. Verified assumptions

Подтверди:

- endpoint contract;
- auth path;
- resolver semantics;
- global vs tenant-scoped identity;
- current BotUser writer absence;
- current consumers.

## B. Final implementation scope

Зафиксируй конкретные файлы/компоненты:

- backend;
- bot identity layer;
- booking integration;
- memory integration;
- consent integration;
- tests.

## C. Excluded scope

Явно перечисли, что НЕ входит:

- DRF-1036 fix;
- proxy history migration;
- full account linking;
- phone;
- OTP;
- Ayla-mediated communication;
- broad eventbus refactor.

## D. Risk gates

Раздели:

```text
Pilot blockers
Production blockers
Known limitations
```

После этого выполняй реализацию.

---

# 19. Финальный owner ruling

```text
J-O1: GO
```

Реализовать минимальный backend `me/identity` contract и reusable bot-side `ensure_ayla_link()`.

```text
J-O2: ACCEPT KNOWN LIMITATION FOR CONTROLLED PILOT
```

Proxy → real account history merge не чинить в DRF-1035. Закрыть отдельной задачей до production использования account linking.

```text
J-O3: IDENTITY-ON-FIRST-DEPENDENT-ACTION
```

Не booking-only.

Но и не буквально «при первом сообщении».

Identity resolution выполняется перед первым действием, которому требуется persistent Ayla subject.

P0 подключение:

```text
booking
memory
consent / revoke
relevant personal-data flow
```

Остальные consumers проверить на совместимость после заполнения поля.

```text
Pilot manual provisioning: GO, limited accounts only.
```

```text
DRF-1036: not Controlled Pilot blocker, but Production blocker.
```

---

# 20. Критерий завершения

DRF-1035 считается решённым только когда новый реальный MAX-пользователь с:

```text
BotUser.ayla_user_id = NULL
```

проходит цепочку:

```text
first Ayla-dependent action
        ↓
ensure_ayla_link()
        ↓
resolved proxy subject
        ↓
BotUser persisted
        ↓
booking succeeds
        ↓
memory uses same subject
        ↓
consent revoke operates on same subject
        ↓
incoming event can find same BotUser
```

и повторный вызов не создаёт новую identity.

Главный архитектурный invariant после исправления:

> **Ayla-dependent capability никогда не должна молча продолжать работу с `BotUser.ayla_user_id = NULL`. До выполнения persistent/customer-specific операции должен существовать единый deterministic identity-resolution path.**

Начни с короткой проверки assumptions по реальному коду, затем реализуй минимальный scope. Не расширяй работу без обнаруженного блокера.
