# DRF-1032 — промпт для оркестратора-архитектора Ayla: клиентская история визитов и повторная запись

Ты работаешь как **оркестратор-архитектор Ayla**. Твоя задача — подготовить DRF-1032 к реализации клиентской истории визитов в MAX/Ayla и функции **«Записаться ещё»**, опираясь на уже существующие backend capabilities и зафиксированные owner decisions.

Это не задача «нарисовать список старых записей». История — часть customer relationship layer Ayla и должна быть спроектирована так, чтобы одновременно:

- давать клиенту полезную память о прошлых визитах;
- давать Ayla основу для персонализации;
- не создавать второй источник истины;
- не обещать возможность повторить то, что больше недоступно;
- не ломаться концептуально при дальнейшем переходе proxy identity → full account;
- не расширять Controlled Pilot лишней инфраструктурой.

Перед изменением кода сначала проверь факты в реальных репозиториях. Не считай старый бриф истиной там, где его можно подтвердить или опровергнуть кодом.

---

# 1. Продуктовый смысл истории

Под словом «история» здесь скрываются как минимум три разных потребителя.

## 1.1. История для клиента

Для клиента это память о том, что с ним действительно происходило.

Практическая ценность:

1. повторить понравившийся визит;
2. вспомнить мастера;
3. вспомнить, когда был прошлый визит;
4. увидеть услугу;
5. увидеть историческую цену;
6. в дальнейшем — использовать прошлый опыт как часть диалога с Ayla.

Типовые вопросы:

```text
«У кого я был в прошлый раз?»

«Когда я последний раз ходил на массаж?»

«Запиши меня ещё раз к тому же мастеру.»

«Что я делал в прошлый раз?»
```

История должна помогать Ayla отвечать на эти вопросы без требования от человека помнить внутренние детали.

---

# 2. История для Ayla

Для Ayla прошлые визиты — один из источников персонализации.

Без customer history невозможно качественно строить утверждения вроде:

```text
«В прошлый раз вы были у Инны.»

«Хотите повторить тот же массаж?»

«Вы уже выбирали эту услугу.»

«В прошлый раз вам подошёл этот мастер.»
```

В дальнейшем история может участвовать в:

- recommendations;
- retention;
- reminders;
- contextual suggestions;
- customer memory;
- loyalty;
- repeat booking;
- service cadence;
- substitution.

Но DRF-1032 не должна превращаться в проект новой recommendation engine.

Текущая задача — корректно открыть пользователю уже существующие факты о его визитах и repeat intent.

---

# 3. История для салона

Для салона customer history потенциально нужна для:

- возвратов;
- loyalty;
- обслуживания постоянного клиента;
- понимания предыдущих визитов;
- continuity обслуживания.

Но на текущем этапе salon-facing customer history **не является scope DRF-1032**.

Если салон сейчас видит только предстоящую запись, не расширяй DRF-1032 до CRM.

Это отдельная продуктовая capability.

---

# 4. Что уже существует на backend

По текущему расследованию backend уже имеет bot-authenticated capabilities как минимум для:

1. списка записей с выбором будущих или исторических;
2. карточки конкретного визита;
3. repeat-intent;
4. возврата прошлых:
   - service;
   - master;
   - price;
5. cursor pagination;
6. спроектированного поведения repeat flow.

Перед реализацией **обязательно подтвердить это по коду**:

- endpoint paths;
- auth classes;
- response schemas;
- status semantics;
- cursor behaviour;
- definition of history;
- definition of completed visit;
- repeat-intent contract;
- current service/master identifiers;
- historical price representation.

Если фактический backend contract отличается от описания — сначала зафиксируй расхождение.

Не проектируй новый endpoint, пока не доказано, что существующего недостаточно.

---

# 5. Текущее поведение bot platform

Сейчас бот использует локальное зеркало записей и показывает только будущие подтверждённые записи.

По расследованию отсутствуют:

- customer-facing history;
- visit detail card;
- repeat booking UX;
- использование backend history API в этом flow.

Это означает, что backend capability уже опережает bot UX.

DRF-1032 должна в первую очередь **подключить существующую capability**, а не заново строить историю.

---

# 6. Owner Decision OD-H1 — Backend является customer-facing source of truth

## Решение: GO

Для всего, что Ayla показывает пользователю как факт о его записях, **источником истины должен быть backend**.

Целевая граница:

```text
                    AYLA BACKEND
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
   Upcoming visits    History      Visit details
          │              │              │
          └──────────────┴──────────────┘
                         │
                         ▼
                    MAX / Client
```

Локальное bot mirror остаётся operational механизмом:

```text
Bot mirror
   │
   ├── reminders
   ├── delivery bookkeeping
   ├── retries
   ├── degraded/offline mechanics
   └── internal operational state
```

## Почему

Уже был подтверждён случай, когда локальное зеркало показывало существующей запись, которую backend больше не считал существующей.

Для истории эта проблема будет только накапливаться:

```text
long-lived mirror
      ↓
stale rows
      ↓
cancelled/deleted/resolved records remain
      ↓
customer sees incorrect past
```

История является долговременной пользовательской правдой, поэтому mirror для неё особенно опасен.

---

# 7. Важное следствие OD-H1

Не делать архитектуру:

```text
Upcoming appointments → bot mirror
History              → backend
```

если нет доказанной operational необходимости.

Команда/экран «Мои записи» должна быть внутренне согласованной.

Предпочтительная модель:

```text
Customer-facing appointments
        ↓
Backend
        │
        ├── upcoming
        └── history
```

Bot mirror не должен быть customer-facing source of truth.

Если переход будущих записей с mirror на backend существенно увеличивает scope DRF-1032, не делай это молча.

Зафиксируй:

- текущий discrepancy;
- минимальный migration path;
- нужен ли отдельный follow-up.

Но конечный invariant должен быть один:

> **Пользовательские факты о записи читаются из backend, а не из локального operational mirror.**

---

# 8. Owner Decision OD-H2 — что считается пользовательской историей

## Решение

По умолчанию показывать **только состоявшиеся визиты**.

Концептуально:

```text
completed / attended
→ SHOW

cancelled by client
→ HIDE by default

cancelled by salon/master
→ HIDE by default

no-show
→ HIDE by default

failed / technical
→ HIDE
```

Это presentation policy.

Она **не означает удаление данных**.

Cancelled/no-show records могут оставаться в backend для:

- analytics;
- operations;
- dispute handling;
- anti-abuse;
- service quality;
- internal history;
- других законных функций.

Но они не должны смешиваться с клиентским списком состоявшихся визитов.

---

# 9. Почему отмены не должны быть в основном списке

Основной customer question:

> «Что у меня действительно было?»

Список:

```text
Массаж — состоялся
Косметология — отменено
Массаж — неявка
Лазер — отменено
Массаж — состоялся
```

плохо отвечает на этот вопрос.

Он превращает полезную память в журнал транзакций.

Особенно no-show может восприниматься как упрёк.

Поэтому основной UX должен отражать реальные посещения.

---

# 10. UX terminology

Предпочтительное пользовательское название:

```text
Мои визиты
```

вместо:

```text
История записей
```

Причина:

«Мои визиты» лучше соответствует policy:

```text
completed visits only
```

Если существующая терминология продукта уже канонизирована иначе — не меняй её автоматически. Проверь UX/copy conventions.

Но semantics должна остаться: основной список = состоявшиеся визиты.

---

# 11. Owner Decision OD-H3 — глубина истории

## Controlled Pilot

Показывать:

```text
последние 5 состоявшихся визитов
```

Без пользовательской pagination.

Не строить на пилоте:

```text
[Показать ещё]
```

и связанный conversation state, если он не нужен для другого существующего flow.

Backend cursor сохраняется и используется согласно API contract, но bot UX на пилоте ограничивается первой страницей/пятью элементами.

---

# 12. Почему пять

Три записи могут быть слишком малой историей для клиента, который пользуется несколькими услугами.

Пять дают достаточно контекста, чтобы:

- найти последнего мастера;
- увидеть несколько услуг;
- повторить недавний визит;
- понять недавнюю последовательность посещений;

и при этом не превращают чат в длинную ленту.

Pagination — P1 после реального использования.

---

# 13. Базовый UX «Мои визиты»

Не копируй этот текст буквально без проверки текущего tone/copy system, но ожидаемая структура примерно такая:

```text
Ваши последние визиты:

1. Массаж спины
   Инна
   12 августа
   2 500 ₽
   [Подробнее] [Записаться ещё]

2. Лимфодренажный массаж
   Денис
   28 июля
   3 500 ₽
   [Подробнее] [Записаться ещё]

3. ...
```

Нужно определить оптимальную плотность для MAX.

Не перегружать карточку второстепенными полями.

Минимально полезные данные:

- service;
- master;
- date/time;
- historical price, если backend действительно её хранит и возвращает;
- action(s).

---

# 14. Карточка визита

Backend уже имеет endpoint карточки визита.

Проверь, какие данные реально возвращаются.

Карточка должна отвечать прежде всего на вопросы клиента:

```text
Что было?
У кого?
Когда?
Сколько тогда стоило?
```

Не выводить внутренние:

- backend IDs;
- proxy identity;
- tenant identifiers;
- operational statuses;
- internal metadata.

Если backend detail endpoint возвращает больше данных, bot должен показывать только customer-relevant subset.

---

# 15. Owner Decision OD-H4 — «Записаться ещё»

## Решение: GO

Repeat booking является частью DRF-1032.

Но принципиально:

> **«Записаться ещё» — это repeat intent, а не гарантированное воспроизведение старой записи.**

Исторические данные описывают прошлое.

Они не доказывают, что тот же вариант существует сейчас.

---

# 16. Historical facts vs current eligibility

Например, история говорит:

```text
service_id = S1
master_id = M1
historical_price = 2500
```

Это означает только:

> в прошлом клиент получил S1 у M1 по цене 2500.

Перед новым booking нужно проверить текущий state:

```text
service still active?
master still active?
master still provides this service?
service/master still available for booking?
current price?
current schedule?
```

Нельзя считать historical IDs current booking entitlement.

---

# 17. Repeat flow

Целевой conceptual flow:

```text
Past visit
    ↓
[Записаться ещё]
    ↓
repeat-intent
    ↓
historical service + master
    ↓
validate current state
    ↓
┌──────────────────────────────┐
│ Service active              │
│ Master active               │
│ Combination valid           │
└──────────────┬───────────────┘
               ↓
      Continue booking
```

Если всё актуально, Ayla должна максимально сократить повторный booking.

Например:

```text
«Повторить массаж у Инны?»

[Да]
[Выбрать другое время/мастера]
```

Точный flow сверить с существующей booking state machine.

Не создавать вторую booking machine для repeat.

---

# 18. Master unavailable

Если прошлый мастер больше не принимает:

не показывать техническую ошибку вроде:

```text
master_not_found
```

или:

```text
cannot create booking
```

Нужна graceful degradation.

Пример semantics:

```text
«Инна сейчас недоступна для этой услуги.
Подобрать другого мастера?»
```

Действия:

```text
[Подобрать мастера]
[Назад]
```

Если Ayla уже имеет substitution/recommendation capability, использовать её.

Не создавать новый recommendation subsystem внутри DRF-1032.

---

# 19. Service unavailable

Если услуга снята с продажи:

```text
«Эта услуга сейчас недоступна.
Могу подобрать похожий вариант.»
```

Дальше использовать существующий discovery/recommendation flow, если он существует.

Не пытаться создать booking со старым service ID.

---

# 20. Master no longer provides service

Отдельный сценарий:

```text
master active
service active
BUT
master no longer provides service
```

Это тоже invalid repeat combination.

Предпочтительное поведение:

```text
«Инна сейчас не принимает на эту услугу.
Подобрать другого мастера или другую услугу у Инны?»
```

Не обязательно реализовывать две ветки, если текущий UX/state machine этого не поддерживает.

Но не выдавать это за system error.

---

# 21. Historical price

Цена в истории — **исторический факт**, не price lock.

Если пользователь видел:

```text
В прошлый раз: 2 500 ₽
```

а сейчас услуга стоит:

```text
2 900 ₽
```

новая запись должна использовать current price.

Не создавать booking по historical price.

Если current price отличается и цена показывается пользователю до подтверждения, изменение должно быть видно.

Пример:

```text
В прошлый раз: 2 500 ₽
Сейчас: 2 900 ₽
```

Не обязательно использовать именно такую copy, но нельзя молча выдавать старую цену за текущую.

---

# 22. Где должна жить current eligibility

На Controlled Pilot допустимо, если bot после `repeat-intent` использует существующие backend APIs для проверки:

- service;
- master;
- service/master relation;
- availability.

Но **канонически bot не должен становиться владельцем бизнес-правил актуальности каталога**.

Invariant:

> Historical endpoint сообщает прошлое. Current backend state определяет, можно ли это повторить сейчас.

Если существующий backend repeat-intent уже возвращает eligibility — использовать её.

Если нет, сначала использовать существующие current-state APIs.

Не расширять backend contract только ради архитектурной красоты, если текущими API можно безопасно выполнить pilot flow.

Если обнаружится дублирование сложных eligibility rules в bot — остановиться и предложить минимальное backend follow-up.

---

# 23. Связь с DRF-1035

DRF-1032 зависит от стабильной Ayla identity.

Для нового MAX user после DRF-1035:

```text
MAX identity
    ↓
Ayla proxy subject
    ↓
BotUser.ayla_user_id
    ↓
history belongs to that Ayla subject
```

Поэтому implementation/deployment order:

```text
DRF-1035 identity bridge
        ↓
verified new-user identity
        ↓
DRF-1032 history
```

Не строить обход identity layer специально для истории.

---

# 24. DRF-1037 — проблема continuity при linking

Подтверждено:

```text
bind_external_identity
```

сейчас не переносит данные proxy subject в full account.

Текущая semantics примерно:

```text
Proxy A
  ├── visits
  ├── bookings
  └── context

       bind

Future resolution
       ↓
Full Account B
```

но старые данные остаются физически на A.

Следствие:

```text
до linking:
history(proxy A) → есть

после linking:
resolution → account B
history(account B) → может быть пустой
```

Это нарушает ожидаемую continuity.

---

# 25. Owner Decision OD-H5 — зависимость от DRF-1037

Историю **не блокировать на Controlled Pilot** до завершения DRF-1037.

Причина:

на Controlled Pilot full account linking не используется.

Proxy subject стабилен:

```text
MAX
 ↓
Proxy A
 ↓
visit 1
visit 2
visit 3
 ↓
history from A
```

Это полноценный и полезный pilot scenario.

Поэтому:

```text
DRF-1035
    ↓
DRF-1032
    ↓
Controlled Pilot history allowed
```

---

# 26. Но account linking имеет жёсткий production gate

Нельзя включать proxy → full-account linking для реальных пользователей, пока DRF-1037 не обеспечивает continuity данных.

Правильная dependency:

```text
DRF-1032 history ───────────────► Controlled Pilot
        ▲
        │
DRF-1035 identity


DRF-1037
   ↓
REQUIRED BEFORE
   ↓
proxy → full account linking
   ↓
real production account lifecycle
```

Не ставить ложную зависимость:

```text
DRF-1037 → history cannot exist
```

Дефект создаёт не история.

Дефект создаёт смена canonical subject без reconciliation принадлежащих ему данных.

---

# 27. Production invariant для DRF-1037

После будущего решения DRF-1037 клиент не должен знать, что когда-то был proxy.

Для него должна существовать одна непрерывная история:

```text
anonymous/external usage
        ↓
proxy identity
        ↓
visits
        ↓
registration/linking
        ↓
full account
        ↓
same visible history
```

Никаких:

```text
«Ваши старые визиты были в другом профиле»
```

если этого можно избежать архитектурно.

Целевой продуктовый invariant:

> **Identity evolution не должна разрушать customer history.**

---

# 28. Не смешивать DRF-1032 и DRF-1037

DRF-1032:

```text
read/display/use history for current subject
```

DRF-1037:

```text
reconcile ownership/history when subject changes
```

Это разные задачи.

Не пытаться реализовать merge/migration внутри DRF-1032.

Но в DRF-1032 явно зафиксировать dependency/gate, чтобы будущий account-linking rollout не нарушил обещание истории.

---

# 29. Empty state

Если состоявшихся визитов нет, не показывать пустой технический список.

Пример semantics:

```text
«У вас пока нет завершённых визитов через Ayla.»
```

После этого желательно дать естественное продолжение:

```text
[Записаться]
```

или conversational CTA согласно текущему UX.

Не говорить «история пуста» техническим языком.

---

# 30. Backend unavailable

Поскольку customer-facing source of truth = backend, не подменять его молча mirror-данными при ошибке backend.

Иначе пользователь может получить stale truth именно тогда, когда backend недоступен.

Предпочтительное поведение:

```text
backend unavailable
        ↓
honest temporary error
        ↓
retry / try later
```

Operational mirror продолжает использоваться для своих внутренних задач, но не становится fallback customer history без отдельного продуктового решения.

---

# 31. Privacy boundary

История должна выдаваться только текущему resolved subject.

Проверить:

```text
authenticated MAX subject A
        ↓
resolved Ayla subject A
        ↓
history A
```

Нельзя принимать произвольный user UUID от conversational caller, если current identity уже известна.

Учитывай найденный DRF-1036, но **не поглощай security remediation в DRF-1032**.

Если существующий history endpoint имеет доказанную cross-subject vulnerability, остановись и эскалируй — не обходи её на bot-side.

---

# 32. Не раскрывать proxy mechanics

Пользователь не должен видеть:

- `is_proxy`;
- `ayla_user_id`;
- external identity;
- internal account type;
- tenant binding;
- merge state.

Для пользователя это просто:

```text
Мои визиты
```

Identity mechanics — внутренняя инфраструктура Ayla.

---

# 33. Minimum Pilot scope

Для Controlled Pilot DRF-1032 должна дать минимум:

```text
1. Получить последние completed visits из backend.
2. Показать максимум 5.
3. Открыть карточку визита.
4. Запустить «Записаться ещё».
5. Проверить current validity repeat intent.
6. Gracefully обработать unavailable master/service.
7. Использовать current price для новой записи.
8. Не использовать bot mirror как customer history.
```

---

# 34. Out of scope для Controlled Pilot

Не добавлять без отдельного решения:

- pagination UX;
- infinite history;
- filters by service/master/date;
- cancelled history screen;
- no-show history screen;
- salon CRM history;
- analytics dashboard;
- history search;
- history export UI;
- new recommendation engine;
- history-based automated marketing;
- proxy → account merge;
- complex cross-channel identity reconciliation;
- phone/OTP;
- new loyalty system.

---

# 35. Pre-implementation investigation

Перед кодом архитектор должен проверить минимум:

## Backend

1. list appointments/history endpoint;
2. detail endpoint;
3. repeat-intent endpoint;
4. authentication;
5. authorization;
6. completed status semantics;
7. cancelled/no-show semantics;
8. cursor contract;
9. historical price;
10. current service/master validation APIs;
11. availability APIs;
12. tenant behaviour;
13. subject identity requirements.

## Bot

1. current «Мои записи» flow;
2. local mirror reads;
3. booking state machine;
4. callback/button conventions;
5. conversation state storage;
6. identity integration after DRF-1035;
7. current error handling;
8. current service/master lookup clients;
9. localization/copy conventions.

---

# 36. Не верить старым предположениям без call-site проверки

Уже был случай в DRF-1035, когда таблица расследования создала ложный вывод о live consent path, а проверка call sites доказала обратное.

Поэтому для DRF-1032:

> Не считать функцию/endpoint активной capability только потому, что она существует в коде.

Для каждого важного вывода проверяй:

```text
definition
+
call sites
+
live path
+
tests
```

Verified no-op является допустимым результатом.

Не изобретай работу ради соответствия старому брифу.

---

# 37. Acceptance Criteria

## AC-1 — Backend source of truth

Customer-facing history читается из backend.

Bot mirror не используется как источник отображаемой истории.

---

## AC-2 — Completed only

Основной список содержит только состоявшиеся визиты согласно canonical backend status semantics.

Cancelled/no-show/failed не попадают в основной список.

---

## AC-3 — Limit 5

На Controlled Pilot отображается максимум пять последних completed visits.

---

## AC-4 — Ordering

Самый недавний состоявшийся визит показывается первым.

---

## AC-5 — Empty state

При отсутствии визитов пользователь получает корректный conversational empty state.

---

## AC-6 — Visit detail

Пользователь может открыть доступную карточку своего визита.

Карточка не раскрывает internal identifiers.

---

## AC-7 — Repeat intent

Для состоявшегося визита доступно действие «Записаться ещё».

---

## AC-8 — Current master valid

Если master/service combination всё ещё валидна, repeat flow использует существующую booking capability.

---

## AC-9 — Master unavailable

Если прошлый мастер больше недоступен, пользователь получает graceful alternative вместо system error.

---

## AC-10 — Service unavailable

Если услуга больше недоступна, пользователь получает graceful alternative.

---

## AC-11 — Combination invalid

Если мастер и услуга существуют, но больше не связаны, старый combination не используется для booking.

---

## AC-12 — Price

Historical price не используется как current booking price.

---

## AC-13 — Backend failure

При недоступности backend stale mirror history не показывается как актуальная правда.

---

## AC-14 — Subject isolation

Пользователь A не может получить историю пользователя B.

---

## AC-15 — Proxy works on pilot

Пользователь с proxy identity после DRF-1035 видит визиты, принадлежащие своему текущему proxy subject.

---

## AC-16 — No pagination

Controlled Pilot не требует stateful «Показать ещё».

---

## AC-17 — Existing booking flow reused

Repeat не создаёт параллельную booking state machine.

---

# 38. Test matrix

Минимально проверить:

```text
1. no completed visits
2. one completed visit
3. five completed visits
4. more than five completed visits
5. completed + cancelled mixed backend data
6. completed + no-show mixed data
7. ordering newest first
8. visit detail
9. repeat valid service/master
10. repeat master inactive
11. repeat service inactive
12. repeat master/service relation removed
13. historical price == current price
14. historical price != current price
15. backend unavailable
16. current subject isolation
17. proxy subject history
18. existing full/current subject history
19. repeated callback/button press
20. stale bot mirror disagrees with backend
```

Критически важный тест:

```text
mirror says appointment exists
backend says it does not
→ customer-facing result follows backend
```

---

# 39. Observability

Не строить новую telemetry platform.

Нужны минимальные сигналы:

```text
history_requested
history_loaded
history_empty
history_failed
visit_detail_opened
repeat_intent_requested
repeat_valid
repeat_master_unavailable
repeat_service_unavailable
repeat_combination_invalid
repeat_booking_started
repeat_booking_completed
repeat_booking_failed
```

На пилоте нужно суметь ответить:

- пользуются ли историей;
- нажимают ли repeat;
- сколько repeat intents валидны;
- сколько упираются в устаревшего мастера/услугу;
- сколько доходят до новой записи.

---

# 40. Product analytics — не переусложнять

Не требуется сейчас строить dashboard.

Достаточно существующих logs/events, если по ним можно получить базовые pilot answers.

Если analytics infrastructure уже есть — подключить минимально.

Не задерживать customer capability ради красивой аналитики.

---

# 41. Deployment dependencies

Предпочтительный порядок:

```text
DRF-1035 backend identity
        ↓
DRF-1035 bot identity
        ↓
new-user E2E verified
        ↓
DRF-1032 history bot integration
        ↓
history E2E
```

Если backend history endpoints уже production-ready, отдельный backend deploy для DRF-1032 может не потребоваться.

Не создавать backend PR только ради того, чтобы задача выглядела симметричной.

---

# 42. Controlled Pilot gate

DRF-1032 можно включать в Controlled Pilot при выполнении:

```text
identity stable
backend history contract verified
completed-only policy enforced
limit 5
repeat current-state validation
graceful stale master/service handling
subject isolation verified
```

DRF-1037 не является blocker истории на Controlled Pilot, пока linking выключен.

---

# 43. Production gate

До включения real proxy → full-account linking:

```text
DRF-1037 MUST be resolved
```

Нельзя создавать пользовательское обещание:

```text
«Ayla помнит ваши визиты»
```

а затем молча терять видимость истории после регистрации/linking.

---

# 44. Что требуется от оркестратора

Сначала проведи короткое investigation/reconciliation.

Верни:

## A. Verified current state

Для backend и bot:

- реальные endpoints;
- реальные call sites;
- status semantics;
- current source of truth;
- repeat contract;
- identity dependency.

## B. Gap analysis

Что уже реализовано и чего реально не хватает.

Раздели:

```text
backend gap
bot gap
product-policy gap
```

## C. Final implementation plan

По файлам/компонентам.

Не абстрактный roadmap, а конкретный минимальный change set.

## D. Contract matrix

Зафиксируй:

```text
history list
visit detail
repeat intent
current eligibility
booking continuation
```

## E. State-machine impact

Покажи, как repeat переходит в существующий booking flow.

Не создавай новую state machine без необходимости.

## F. Tests

Дай точный список unit/integration/E2E tests.

## G. Risks

Раздели:

```text
Pilot blocker
Production blocker
Known limitation
Follow-up
```

## H. Owner decisions

Не возвращай владельцу уже решённые вопросы:

```text
Source of truth = backend
History = completed visits
Limit = 5
Pagination = no for pilot
Repeat = yes
DRF-1037 = not history pilot blocker
DRF-1037 = account-linking production blocker
```

Эскалируй только новые реальные product decisions.

---

# 45. Зафиксированные owner decisions

```text
OD-H1
Customer-facing source of truth = Ayla backend.
GO.
```

```text
OD-H2
Основная история = только состоявшиеся визиты.
Cancelled/no-show скрыты по умолчанию.
GO.
```

```text
OD-H3
Controlled Pilot = последние 5 completed visits.
Без pagination.
GO.
```

```text
OD-H4
«Записаться ещё» = GO.
Это repeat intent, не гарантированное воспроизведение.
Current eligibility проверяется заново.
```

```text
OD-H5
DRF-1037 не блокирует history на Controlled Pilot.
Но блокирует production rollout proxy → full-account linking.
```

---

# 46. Главный архитектурный invariant

После реализации должно быть истинно:

> **Backend хранит пользовательскую правду о визитах; bot отображает её и оркестрирует действия, но не создаёт собственную конкурирующую историю.**

И второй invariant:

> **Исторический визит описывает прошлое. Новая запись всегда создаётся по текущему состоянию услуги, мастера, цены и доступности.**

И третий:

> **Эволюция identity в будущем не должна разрушать видимую клиенту историю; этот production invariant принадлежит DRF-1037.**

---

# 47. Критерий хорошего результата

Новый пользователь Controlled Pilot после нескольких реальных визитов должен иметь возможность написать:

```text
«Покажи мои визиты»
```

и получить последние реальные состоявшиеся визиты из backend.

После этого он должен иметь возможность выбрать:

```text
[Записаться ещё]
```

и Ayla должна:

1. понять, что именно он хочет повторить;
2. проверить текущее состояние;
3. если всё актуально — продолжить существующий booking flow;
4. если мастер/услуга больше недоступны — предложить разумную альтернативу;
5. использовать текущую цену;
6. не раскрывать внутреннюю identity механику;
7. не полагаться на stale bot mirror.

Начни с проверки реального кода и call sites. Не начинай с реализации. Не расширяй scope «заодно». Если backend уже реализовал нужную capability — переиспользуй её.
