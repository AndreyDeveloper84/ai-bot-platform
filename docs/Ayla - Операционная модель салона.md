**You:**

У нас не продумана вся операционная часть мастера/салона. ![](https://chatgpt.com/images/ecosystem/apps/github/icon.png)GitHub посмотри репозитории необходимые, надо уложиться в неделю

* * *

**ChatGPT:**

Посмотрел GitHub и связанные handoff-документы. Основной вывод: **операционная модель у нас уже довольно подробно спроектирована, но она значительно опережает фактическую реализацию**. Поэтому за неделю реально не «сделать весь кабинет салона», а собрать **рабочее операционное ядро**, на котором салон сможет реально прожить день без YClients и без ручных обходов.

### Что уже есть

Главный репозиторий для этой задачи — **`AndreyDeveloper84/ai-bot-platform`**. `ayla-ai-core` для недельного операционного спринта почти не нужен: там AI-runtime, а не управление салоном.

В `ai-bot-platform` уже есть хорошая база:

* `apps/booking` — модель `BookingRequest`, статусы записи, master/service FK, `visit_at`, защита от двойной записи на уровне БД.
* `apps/bookings` — callbacks, reminders, follow-ups, escalation, pending actions.
* `apps/catalog` — `CatalogService`, `CatalogMaster` и уже существующий `MasterService`, то есть связь **какой мастер какие услуги выполняет**.
* `CatalogMaster` уже получил `invite_status`, `mode`, `photo_url`, `archived_at`, `max_handle`; это не пустой концепт.
* есть YClients client + webhook integration.

Но `adminconsole` практически пустой — сейчас там только каркас приложения. Это один из главных сигналов: backend-кирпичи есть, **операционной оболочки почти нет**.

При этом дизайн уже описывает сильно больше: управление мастерами, schedule management и полноценную mobile-role мастера. В Master Management заложены roster, приглашение, услуги мастера, deactivate/reactivate и обработка будущих записей. В Schedule Management уже определены `WorkingHours`, `ScheduleException`, `TimeBlock`, `ScheduleChangeRequest` и формула свободного слота:

**WorkingHours − Exceptions − TimeBlocks − Bookings**.

То есть проблема сейчас не «мы не знаем, что строить». Проблема — **слишком широкий замысел, который надо резко обрезать до вертикального рабочего среза.**

## Что должно войти именно в эту неделю

Я бы назвал спринт:

**Salon Operations MVP — One Working Day**

Его критерий очень простой:

> Владелец небольшого салона должен суметь добавить мастеров и услуги, задать рабочее время, увидеть день, вручную создать/изменить/отменить запись, закрыть время мастера и быть уверенным, что Ayla больше не предложит занятый слот.

Это даст нам настоящий end-to-end продукт.

### P0 — обязательно за неделю

| Контур | Что должно заработать |
| --- | --- |
| Мастера | список, создание, редактирование, активировать/деактивировать |
| Услуги мастера | назначить каждому мастеру выполняемые услуги |
| Рабочие часы | недельный график Пн–Вс |
| Исключения | выходной/болезнь/отпуск/изменённые часы на конкретную дату |
| Блок времени | обед, личное время, технический перерыв |
| Расписание | день + ближайшие записи мастеров |
| Ручная запись | администратор создаёт запись |
| Изменение записи | время/мастер |
| Отмена | отменить запись без удаления истории |
| Availability | свободные слоты рассчитываются из реального операционного состояния |
| Защита | невозможно получить двойную активную бронь |
| Permissions | owner/admin могут управлять; master пока только свой минимум |

Всё остальное нельзя позволить затянуть этот спринт.

## Что сознательно вырезать

Вот здесь экономится неделя.

**Не делаем сейчас** полноценные:

* earnings мастера;
* reviews/feedback;
* substitution;
* offboarding workflow;
* device re-auth;
* internal chat;
* подробную аналитику;
* loyalty;
* marketing;
* advanced notification settings;
* полноценную master inbox;
* AI-drafted replies;
* сложное approval изменение графика;
* month calendar;
* drag&drop календарь;
* мультифилиальность;
* кастомные роли;
* сложный YClients conflict-resolution UI.

Это всё существует в handoff-слое как будущая операционная система, но не является условием «салон способен работать завтра».

Есть ещё важное противоречие. `master-mobile-handoff` проектирует **ACTIVE master role** с dashboard, сообщениями клиентов, профилем и прочим и прямо оценивает полную реализацию в 6–8 недель. Поэтому пытаться реализовать этот документ целиком за неделю будет ошибкой.

Для этой недели мастер должен получить урезанную роль:

**Сегодня → ближайшие записи → свой график → запросить/отметить отсутствие.**

Не CRM в телефоне.

---

# Как я бы построил архитектуру

Нам не нужен ещё один `master_operations` giant-app.

Я бы сделал так:

```
Catalog ├── CatalogMaster ├── CatalogService └── MasterService
Scheduling ├── WorkingHours ├── ScheduleException └── TimeBlock
Booking └── BookingRequest       ├── master       ├── service       ├── visit_at       ├── duration_min       └── status
            ↓
AvailabilityService(master, service, date)            ↓ WorkingHours - Exceptions - TimeBlocks - confirmed Bookings            ↓        free slots
```

То есть появляется один новый доменный кусок — условный `apps/scheduling`.

**Не надо помещать расписание внутрь booking.** Booking отвечает за состоявшуюся/планируемую запись. Scheduling отвечает за способность мастера вообще принять клиента.

Это важное разделение.

---

# План на 7 дней

### День 1 — доменная модель и API foundation

Сделать:

`WorkingHours`  
`ScheduleException`  
`TimeBlock`

и минимальные serializers/services/API.

Плюс привести к единому контракту:

`CatalogMaster`  
`MasterService`  
`BookingRequest`

В этот же день написать главный invariant:

```
master может быть предложен клиентуIFFmaster activeAND invite/bookable state validAND performs serviceAND working at requested timeAND no exceptionAND no time blockAND no overlapping booking
```

Не UI сначала. Сначала этот контракт.

### День 2 — Availability Engine

Это самая важная техническая задача недели.

Например:

```Python
get_available_slots(    tenant_id,    master_id,    service_id,    date,)
```

Алгоритм:

```
рабочий интервал     ↓исключение на дату     ↓вычитаем TimeBlock     ↓вычитаем confirmed BookingRequest     ↓учитываем duration услуги     ↓учитываем buffer     ↓нарезаем доступные start times
```

Обязательно тестами закрыть:

* два мастера работают одновременно;
* один не работает;
* отпуск;
* обед;
* 60-минутная услуга не помещается в 30-минутное окно;
* запись 11:00–12:00 блокирует слот;
* отменённая запись снова освобождает время;
* concurrent create не создаёт две записи.

Последнее особенно важно: существующий `UniqueConstraint(master, visit_at)` защищает только одинаковое `visit_at`, но **не гарантирует отсутствие перекрывающихся интервалов**. Например:

```
10:00–11:3010:30–11:30
```

имеют разные `visit_at`.

Это надо исправить в рамках P0.

### День 3 — Team + Services

Один простой экран:

```
Команда
АннаМассажистСегодня 10:00–19:006 услуг● Работает
[Открыть]
```

Карточка:

```
Анна Петрова
Активна ✓
Услуги[x] Классический массаж[x] Спортивный массаж[ ] Лазерная эпиляция
ГрафикПн 10–19Вт 10–19...
[Изменить график]
```

Никакой сложной MM1/MM2/MM3/MM4 реализации целиком.

Мы используем уже существующие `CatalogMaster` + `MasterService`.

### День 4 — Schedule Day View

Не неделю, месяц и три режима из handoff.

**Только Day View.**

Пример:

```
Сегодня, 20 августа
         Анна       Мария09:00    закрыто    свободно10:00    Иван       свободно11:00    Иван       Ольга12:00    обед       Ольга13:00    свободно   свободно
```

Actions:

```
+ Запись+ Заблокировать времяИзменить записьОтменить запись
```

Это и есть настоящий салонный операционный интерфейс.

### День 5 — ручные записи и изменения

Admin должен иметь возможность:

```
КлиентТелефонУслугаМастерДатаВремяКомментарий
```

И:

**Создать → перенести → отменить.**

Все три операции должны идти через один application/service layer, а не прямые `.objects.create()` из разных интерфейсов.

Например:

```Python
BookingService.create(...)BookingService.reschedule(...)BookingService.cancel(...)
```

Bot, admin UI и будущий master UI должны вызывать один и тот же domain layer.

### День 6 — интеграция с Ayla + YClients boundary

Проверить end-to-end:

```
Admin меняет график        ↓Availability Engine        ↓show_slots()        ↓Ayla показывает только реальные слоты        ↓клиент записывается        ↓BookingRequest        ↓слот исчезает
```

А для YClients пока вводим правило:

```
YC-connected tenant:YClients = source of truth bookings
Template tenant:Ayla = source of truth bookings + schedule
```

Не надо за неделю строить двунаправленную distributed-calendar систему.

### День 7 — hardening

Никаких новых features.

Только:

* permissions;
* tenant isolation;
* race conditions;
* timezone;
* overlap tests;
* migrations;
* audit;
* empty states;
* error states;
* E2E;
* smoke staging;
* demo salon.

---

# Что получится через неделю

У владельца появится такой минимальный контур:

```
                     SALON OPERATIONS
              ┌──────────────┐              │    Team      │              │ masters      │              │ services     │              └──────┬───────┘                     │                     ▼              ┌──────────────┐              │  Scheduling  │              │ hours        │              │ exceptions   │              │ blocks       │              └──────┬───────┘                     │                     ▼              ┌──────────────┐              │ Availability │              └──────┬───────┘                     │           ┌─────────┴─────────┐           ▼                   ▼       Admin schedule       Ayla booking           │                   │           └─────────┬─────────┘                     ▼              ┌──────────────┐              │   Booking    │              └──────────────┘
```

И это уже **операционная система записи**, а не набор демонстрационных экранов.

## Самое важное решение

Я бы сейчас **не открывал новый большой этап “реализовать все master handoffs”**.

Создал бы один umbrella:

> **Wave: Salon Operations MVP**

с шестью P0 streams:

1. Scheduling Domain
2. Availability Engine
3. Team & Service Assignment
4. Day Schedule
5. Manual Booking Lifecycle
6. End-to-End Operational Acceptance

А все `earnings`, `reviews`, `time-off advanced`, `substitution`, `offboarding`, `master chat`, `mobile full experience` — следующая Wave.

С учётом того, что кодовая база уже содержит Booking, Catalog, MasterService и YC integration, **это выглядит достижимым за неделю при параллельной работе 2–3 агентов**. Самая опасная часть — не frontend, а корректный availability/overlap contract.

Следующим шагом я бы уже не обсуждал концепцию, а **прошёл по коду глубже и составил точную gap-matrix `есть / частично / нет / переделать` по каждому из этих шести P0-потоков**, после чего можно сразу декомпозировать их в Linear на недельный спринт.

* * *

**You:**

![](https://chatgpt.com/images/ecosystem/apps/github/icon.png)GitHub https://github.com/AndreyDeveloper84/beautygo_backend посмотри бэк

* * *

**ChatGPT:**

Да. И после просмотра `beautygo_backend/dev` картина **существенно меняется**.

`beautygo_backend` — это не старый зачаточный backend. В `dev` он уже является **каноническим backend Ayla для booking + payments + catalog + identity/PII**. Именно здесь должен жить операционный контур мастера/салона, а `ai-bot-platform` должен его потреблять через контракты/события, а не дублировать состояние. Это прямо зафиксировано в README репозитория.

### Что уже реально сделано

В backend уже зарегистрированы полноценные приложения `services`, `appointments`, `reviews`, `payments`, `billing`, `notifications`, `tenants`, `analytics` и другие. Админка уже содержит отдельные разделы «Мастера», «Услуги», «Записи», «Расписание», «Выходные».

Сам мастер тоже уже нормальная доменная сущность — `SpecialistProfile`. В ней есть tenant, профиль, timezone, `is_booking_enabled`, статус, рейтинг, availability и, что особенно важно, уже заложен двухрежимный booking source:

```
ayla_local → Ayla сама хранит расписание и записиyclients   → YClients является SoR
```

Плюс уже существуют `yclients_company_id` и `yclients_staff_id`.

То есть то, что я до этого предлагал проектировать заново как границу YClients/Ayla, **уже существует**.

Ещё важнее: расписание тоже уже существует.

В `appointments.models` уже есть:

* `SpecialistWorkingHours`;
* рабочий/нерабочий день;
* `start_time/end_time`;
* `break_start/break_end`;
* `SpecialistTimeOff`;
* интервалы отсутствия мастера.

### Booking engine тоже уже гораздо дальше, чем я предполагал

`Appointment` — это уже полноценная запись:

```
PENDINGAWAITING_PAYMENTCONFIRMEDIN_PROGRESSCOMPLETEDCANCELLEDNO_SHOW
```

Есть:

* master;
* tenant;
* service / salon_service;
* start/end;
* snapshot цены и длительности;
* first visit;
* cancellation reason;
* `cancelled_by`;
* optimistic version;
* audit переносов через `AppointmentRevision`;
* state machine.

И application layer **уже существует**:

```
CreateBookingServiceCancel/RescheduleServiceAvailabilityQueryServicebooking guards
```

Это очень хорошая новость: нам не надо тратить неделю на создание booking architecture.

Более того, уже есть abstraction для внешнего календаря: `ExternalBusyIntervalProvider`, который добавляет занятые интервалы YClients в существующий availability pipeline без изменения `SlotBuilderService`/`AvailabilityQueryService`.

---

# Значит, реальная задача недели другая

Нам **не нужно строить операционное ядро**.

Оно уже в значительной степени построено.

Нужно довести до рабочего состояния:

> **Salon Operations API + Pro UI**

То есть дать владельцу/администратору/мастеру интерфейсы к уже существующей доменной модели.

Я бы теперь поменял недельный scope на такой:

| Область | Backend | Что надо за неделю |
| --- | --- | --- |
| Мастер | 🟢 уже есть | owner/admin management API |
| Услуги мастера | надо глубже проверить | CRUD/mapping для салона |
| Рабочие часы | 🟢 модель есть | нормальный Pro API |
| Перерывы | 🟢 уже есть | Pro API/UI |
| Отпуск/больничный | 🟢 модель есть | CRUD API |
| Availability | 🟢 engine есть | проверить end-to-end |
| Запись | 🟢 полноценная | staff/manual-create surface |
| Перенос | 🟢 полноценный | staff permissions/API |
| Отмена | 🟢 полноценная | staff permissions/API |
| No-show | 🟢 уже есть | master/admin action |
| Завершение визита | 🟢 уже есть | operational action |
| YClients busy | 🟢 foundation есть | проверить wiring |
| Дневной календарь | 🔴 UI | сделать |
| Управление командой | 🔴/🟡 | сделать минимальный UI/API |
| Клиенты салона | пока надо проверить | P0 gap-кандидат |
| Walk-in | надо проверить | P0 gap-кандидат |

## И здесь появляется более правильная модель рабочего дня

Операционный MVP должен позволить пройти цепочку:

```
Владелец   ↓добавляет мастера   ↓назначает услуги   ↓задаёт график   ↓администратор видит календарь   ↓создаёт ручную запись   ↓Ayla учитывает её как busy   ↓клиент записывается через Ayla   ↓мастер видит визит   ↓клиент пришёл   ↓IN_PROGRESS   ↓COMPLETED
```

и альтернативные ветки:

```
           ┌→ CANCELLEDAppointment├→ RESCHEDULE           └→ NO_SHOW
```

Вот **это** я считаю хорошим acceptance-критерием недели.

### Есть ещё одна важная архитектурная поправка

Не надо создавать отдельный `scheduling` app, как я предложил выше.

У вас уже сложилось разумное ownership:

```
users└── SpecialistProfile
services├── salon catalog└── external busy intervals
appointments├── Appointment├── SpecialistWorkingHours├── SpecialistTimeOff├── Availability├── CreateBooking├── Cancel└── Reschedule
```

Для недели лучше **не рефакторить эту границу**. Она уже работающая и достаточно логичная.

А `ai-bot-platform` должен выглядеть так:

```
                    ┌─────────────────────┐                    │  beautygo_backend   │                    │                     │                    │ CANONICAL STATE     │                    │                     │                    │ masters             │                    │ services            │                    │ schedules           │                    │ appointments        │                    │ payments            │                    └─────────┬───────────┘                              │                         API / events                              │                    ┌─────────▼───────────┐                    │  ai-bot-platform    │                    │                     │                    │ conversations       │                    │ AI orchestration    │                    │ memory / tools      │                    │ channel runtime     │                    └─────────────────────┘
```

Это согласуется и с самим README backend.

## Что я предлагаю сделать теперь

Теперь уже имеет смысл сделать **не поверхностный просмотр, а полный технический аудит операционной части `beautygo_backend/dev`**.

Я бы прошёл конкретно:

`users → services → appointments → tenants → notifications → payments/billing → internal API → tests`

и на выходе сделал одну gap-matrix:

```
Функция                              Модель   Service   API   Permissions   Tests   UI──────────────────────────────────────────────────────────────────────────────Создать мастера                       ✅       ?         ?        ?           ?     ❌Назначить услуги                     ?        ?         ?        ?           ?     ❌Изменить недельный график             ✅       ?         ?        ?           ?     ❌Добавить отпуск                       ✅       ?         ?        ?           ?     ❌Ручная запись администратора          ✅       ✅        ?        ?           ?     ❌Перенос                               ✅       ✅        ✅       ?           ?     ❌Отмена                                ✅       ✅        ✅       ?           ?     ❌No-show                               ✅       ✅/model  ?        ?           ?     ❌Завершить визит                       ✅       ✅/model  ?        ?           ?     ❌Day calendar                          ✅ data  ✅ engine ?        ?           ?     ❌
```

**И уже из этой матрицы можно очень точно вырезать недельный P0 без переписывания того, что уже существует.**

Сейчас я бы продолжал именно так: глубоко разбирать `beautygo_backend/dev`, потому что после увиденного я думаю, что **за неделю можно сделать заметно больше операционки, чем я оценивал в прошлом сообщении**.

* * *

**You:**

Разбери глубоко необходимые части

* * *

**ChatGPT:**

Разбираю не по файлам подряд, а по операционным сценариям: **команда → услуги → график → доступность → запись → перенос/отмена → визит → уведомления → tenant/permissions**. По каждому отмечу, что уже production-ready, что частично, и что реально блокирует недельный запуск.

![](/images/ecosystem/apps/github/icon.png)Проверены backend-директории, API, индексы, тесты и URL ветки dev

Уже нашёл первый конкретный разрыв: **мастер умеет сам управлять своим расписанием через `/specialists/me/...`**, но отдельный salon/owner API для управления всеми мастерами практически не оформлен — `/api/v1/masters/` сейчас зарезервирован и содержит только внутренний lookup по YClients staff IDs. При этом self-service schedule API у мастера уже есть. Это значит, что недельный P0 будет во многом про **owner/admin projection поверх уже готовых моделей и сервисов**, а не про новый booking engine.

![](/images/ecosystem/apps/github/icon.png)Изучение API расписания, услуг, ролей и бронирования

* * *

**You:**

Очень долго

* * *

**ChatGPT:**

Да, затянул. Основное уже нашёл, дальше можно без долгого обхода.

Короткий вывод по `beautygo_backend/dev`: **ядро операционки уже есть примерно на 60–70%**. Готовы модели мастера, расписания, отпусков/блоков, booking engine, availability, walk-in, перенос, отмена, завершение визита, no-show, tenant-scoping и базовые роли.

Главные реальные пробелы на неделю сейчас такие:

* **Нет нормального owner/admin API управления командой салона.** Мастер умеет управлять собой через `/specialists/me/...`, но `/masters/` фактически пустой и содержит только внутренний YClients lookup.
* **Каталог уже перестраивается правильно:** `SalonService → SpecialistService`, то есть услуга салона отдельно от назначения её мастеру. Это надо использовать, а не старый `Service`, где услуга жёстко принадлежит одному мастеру.
* **График мастера уже готов практически полностью:** недельный график, перерыв, time-off, валидации, cache invalidation, запрет поставить отсутствие поверх активных записей.
* **Booking lifecycle уже сильный:** create, walk-in, cancel, reschedule, complete, no-show, optimistic locking и idempotency. Это нельзя переписывать.
* **Availability уже считает реальные busy intervals**, график, перерыв и внешнюю занятость. При создании записи есть повторная проверка конфликта внутри транзакции плюс advisory lock — это хорошая защита от двойной записи.
* **Роли салона уже заложены** через `TenantUserRelationship`: `customer / staff / admin`, и есть `IsTenantAdmin`. То есть новую RBAC-систему за неделю не строим.

Поэтому недельный scope я бы теперь жёстко сократил до **5 задач**:

1. `Salon Team API` — owner/admin видит мастеров салона, добавляет/деактивирует, меняет статус.
2. `Salon Service Assignment API` — salon services + назначение мастерам через `SpecialistService`.
3. `Admin Schedule API` — администратор меняет график/time-off любого мастера своего tenant, используя уже существующую логику.
4. `Salon Day Calendar API` — один endpoint, который отдаёт мастеров + записи + blocks на день.
5. `Admin Booking Actions` — создать ручную запись за мастера, перенести, отменить, complete/no-show с tenant-admin permissions.

**Не трогаем** новый availability engine, новую booking state machine, earnings, reviews, substitution, internal chat, offboarding и сложный mobile master dashboard.

Если сделать только это, к концу недели салон уже сможет реально работать внутри Ayla.

* * *

**You:**

А что с ux ui экранов, макетами, документами описывающими взаимодействия мастера и Ayla

* * *

**ChatGPT:**

Да. И здесь ситуация **хуже, чем с backend**: UX/UI и документы у нас есть в большом объёме, но они сейчас описывают **слишком большой продукт мастера**, а не недельный операционный MVP.

Я поднял основные handoff’ы. Картина такая.

### Что уже спроектировано

`master-mobile-handoff` — это почти полноценное отдельное приложение мастера. Там есть:

* onboarding по приглашению;
* главный dashboard;
* расписание day/week/month;
* редактирование профиля;
* список клиентских разговоров;
* conversation detail;
* notification settings;
* app settings;
* отдельные backend contracts для dashboard, schedule, availability, conversations, profile и notifications.

То есть **“мастер ↔ Ayla” уже описан как полноценная ежедневная рабочая среда**, а не просто календарь.

`master-management-handoff` описывает сторону владельца/администратора:

* список мастеров;
* приглашение мастера;
* карточку/редактирование;
* матрицу «услуга ↔ мастер»;
* деактивацию с обработкой существующих записей;
* reactivation;
* audit.

`schedule-management-handoff` ещё шире:

* основной календарь;
* desktop + mobile;
* редактирование рабочего времени;
* exception calendar;
* ручная запись;
* time block;
* запрос мастера на изменение графика;
* day/week/month/per-master views;
* YClients sync-conflicts.

`master-time-off-handoff` вообще проектирует самостоятельный workflow:

* sick day;
* vacation;
* planned leave;
* recurring schedule;
* история отсутствий;
* approval администратором;
* уведомления через Ayla;
* автоматическая обработка затронутых клиентов.

И отдельно есть `master-admin-internal-chat-handoff` — полноценный внутренний мессенджер мастера с салоном.

То есть проблема **не в отсутствии UX**. Проблема наоборот — UX уже расползся на продукт примерно на несколько месяцев разработки.

## Что я считаю ошибкой сейчас

Если начать реализовывать эти handoff’ы буквально, мы неделю потеряем.

Например, один только `master-mobile-handoff` содержит:

```
M0 onboardingM1 dashboardM3 scheduleM4 profileM5 conversationsM6 conversationM7 notificationsM8 settings
```

А `master-management` ещё пять больших экранов.

А `schedule-management` — ещё шесть.

И каждый из этих документов проектировался как самостоятельный production-модуль.

Нам этого сейчас не надо.

---

# Какой UX нужен именно на ближайшую неделю

Я бы сделал **один очень маленький Master Pro MVP**.

У мастера всего **3 основных раздела**:

```
Сегодня        Расписание        Профиль
```

А владельцу/администратору:

```
Сегодня        Команда           Услуги
```

Это практически весь недельный продукт.

### 1. Экран мастера «Сегодня»

Это главный экран, и он важнее всех dashboards из старых документов.

Например:

```
Доброе утро, Анна
Сегодня, 14 августа
10:00Классический массажМария К.60 мин
11:30Массаж спиныАлексей П.30 мин
13:00–14:00Перерыв
15:00Лимфодренажный массажЕлена С.
────────────────
Следующий клиент через 42 мин
[Посмотреть запись]
```

Из записи:

```
Елена С.
Лимфодренажный массаж15:00–16:002 500 ₽
Комментарий клиента...
[Клиент пришёл][Перенести][Отменить]
•••Не пришёл
```

Вот это настоящий рабочий UX.

Не аналитика, не красивые KPI.

### 2. Расписание мастера

В первую неделю:

**только день + неделя**.

Не месяц.

```
← 14 августа →
0910  Мария11  Алексей1213  Перерыв1415  Елена161718
```

Кнопка:

```
+ Заблокировать время
```

И:

```
Изменить рабочий графикЗапланировать выходной
```

Причём backend для большей части этого уже существует.

### 3. Профиль мастера

Минимально:

```
ФотоИмяОписание
Мои услуги8 услуг
Принимать новые записи     ON
[Редактировать]
```

Без огромного settings hub.

---

# UX владельца/администратора

Тут тоже нужно обрезать.

### Экран «Сегодня»

Я считаю его вообще центральным экраном салона.

```
Сегодня · 14 августа
Все мастера ▼
Анна10:00 Мария       Классический массаж11:30 Алексей      Спина15:00 Елена        Лимфодренаж
Ирина09:00 Ольга        Лазер10:00 свободно11:00 Наталья      Лазер
[+ Новая запись]
```

На desktop это можно сделать колонками:

```
          Анна          Ирина          Ольга
09:00                   Ольга10:00    Мария          свободно11:00    Алексей        Наталья12:00    ...13:00    Перерыв
```

Это фактически урезанный `Schedule Management S1`, но без лишней сложности.

### Экран «Команда»

Берём `MM1`, но сильно сокращаем:

```
Команда                           + Добавить
Анна ПетроваМассажист8 услуг · сегодня 10:00–19:00● Активна
Ирина СмирноваКосметолог12 услуг · выходной● Активна
```

Карточка мастера:

```
Анна Петрова
Статус      АктивнаЗапись      Включена
Услуги      8 →График      →Выходные    →
[Редактировать]
```

### Экран «Услуги»

Вот здесь новый `SalonService → SpecialistService` backend отлично ложится в UX:

```
Классический массаж60 минут · 2 500 ₽
Выполняют:✓ Анна✓ Денис□ Ирина
```

То есть не нужно заставлять владельца создавать отдельный экземпляр услуги для каждого мастера.

---

# Что делать с Ayla

Вот это важнейшая часть твоего вопроса.

Сейчас документы местами проектируют Ayla то как ассистента клиента, то как ассистента салона, то как помощника мастера. Предыдущий UX-аудит уже обнаружил эту проблему: Ayla-first модель ещё не полностью протащена через старые handoff’ы; активные и legacy-концепции перемешаны.

Я бы зафиксировал очень простой принцип:

> **Мастер не “разговаривает с Ayla вместо интерфейса”. Ayla помогает мастеру в тех ситуациях, где сообщение быстрее интерфейса.**

Например.

Утром:

> «Анна, доброе утро. Сегодня 6 записей. Первая — Мария в 10:00. Между 13:00 и 15:00 свободное окно.»

Если клиент отменился:

> «Запись Елены на 15:00 отменена. Освободилось окно 15:00–16:00.»

Если мастер заболел:

Мастер пишет:

> «Я сегодня заболела»

Ayla:

> «Поняла. На сегодня у тебя 5 записей. Могу отправить запрос администратору на закрытие дня.»

И дальше **явное действие**:

```
[Отправить запрос][Открыть расписание]
```

То есть Ayla — contextual assistant поверх операционки.

Не второй способ управлять всей системой.

---

# Что из старых handoff'ов оставить

На неделю я бы сделал так:

| Документ | Что берём |
| --- | --- |
| `master-mobile` | M0, сильно урезанный M1, M3, часть M4 |
| `master-management` | MM1, MM2, MM3, MM4 |
| `schedule-management` | S1 day/week, S2, S4 manual booking, S5 block |
| `master-time-off` | только простой time-off, без сложной approval-machine |
| `salon-onboarding` | создание салона → услуги → команда → расписание |
| `master-admin-chat` | **не берём** |
| earnings | не берём |
| reviews | не берём |
| substitution | не берём |
| offboarding | не берём |

И вот здесь я бы **не выбрасывал старые документы**. Мы делаем новый небольшой canonical документ:

# `Salon Operations MVP — UX/Interaction Spec`

В нём должны быть только:

```
ROLE: OWNER/ADMINA1 TodayA2 TeamA3 Master DetailA4 ServicesA5 Manual Booking
ROLE: MASTERM1 TodayM2 Appointment DetailM3 ScheduleM4 Working Hours / Time OffM5 Profile
AYLA TOUCHPOINTSB1 Morning briefingB2 New bookingB3 CancellationB4 Schedule conflictB5 Sick/day-off requestB6 Next appointment reminder
```

Всего **10 экранов + 6 Ayla touchpoints**.

Вот это уже реально можно спроектировать, нарисовать и реализовывать параллельно за неделю.

И самое важное: **нам сейчас нужен не ещё один большой handoff, а набор конкретных финальных макетов этих 10 экранов**, привязанный к существующему backend API. После этого frontend-агенты смогут работать без трактовок.

* * *

**You:**

Что надо сразу предусмотреть, чтобы не запутаться? Надо ли смотреть на поток данных? На каком экране какие данные нужны

* * *

**ChatGPT:**

Да. **Именно это надо зафиксировать до того, как параллельно раздать работу backend, frontend и дизайну.** Иначе через неделю у нас будут красивые экраны, готовые API и модели, которые не совпадают между собой.

Я бы сейчас перестал мыслить отдельными «макетами» и «эндпоинтами» и описал **операционный контур целиком: сценарий → экран → данные → действие → изменение состояния → кто узнаёт об изменении**.

### 1. Сначала — карта данных

Нужно определить несколько канонических сущностей и больше не придумывать экранные аналоги тех же данных:

```
Tenant / Salon│├── Staff / Specialist│    ├── Profile│    ├── WorkingHours│    ├── TimeOff│    └── ServiceAssignments│├── SalonService│    └── SpecialistService│├── Customer│└── Appointment     ├── Customer     ├── Specialist     ├── Service     ├── start/end     ├── status     └── history
```

Это особенно важно, потому что backend уже движется к модели `SalonService → SpecialistService`, а `TenantUserRelationship` уже разделяет `customer / staff / admin`.

То есть мы **не проектируем данные от экранов**. Экраны являются разными представлениями одного домена.

---

## 2. Да — нужен Screen → Data Contract

Для **каждого экрана** я бы завёл простую таблицу.

Например, `Master / Today`:

| Что отображаем | Источник |
| --- | --- |
| имя/фото | SpecialistProfile |
| дата | device/tenant timezone |
| рабочее время | SpecialistWorkingHours |
| записи | Appointment |
| клиент | User/Customer projection |
| услуга | SalonService snapshot |
| время | Appointment.start/end |
| статус | Appointment.status |
| перерыв | WorkingHours |
| отсутствие | SpecialistTimeOff |

И отдельно действия:

| Action | Command |
| --- | --- |
| открыть запись | GET appointment |
| клиент пришёл | transition |
| завершить | complete |
| не пришёл | no-show |
| отменить | cancel |
| перенести | reschedule |
| заблокировать время | create TimeOff/block |

У нас уже есть `complete`, `no-show`, `cancel`, `reschedule`, причём с защитой конкурентных изменений. Поэтому UI должен подстраиваться под эти реальные domain commands, а не придумывать свою state machine.

---

## 3. Нужна отдельная карта потоков данных

Например, мастер заболел.

```
MASTER   │   │ "Сегодня не работаю"   ▼Master App / Ayla   │   │ create time-off   ▼beautygo_backend   │   ├── SpecialistTimeOff   │   ├── affected appointments   │   ├── invalidate availability   │   └── OutboxEvent           │           ▼     ai-bot-platform           │           ├── уведомление           ├── диалог           └── дальнейшее действие
```

Вот такие схемы нужны для **ключевых операций**, а не для каждой кнопки.

Минимум:

1. новая запись;
2. ручная запись;
3. перенос;
4. отмена;
5. no-show;
6. complete;
7. изменение рабочего времени;
8. time-off;
9. назначение услуги мастеру;
10. отключение мастера.

---

# 4. И обязательно определить Source of Truth

Это одно из самых важных решений.

Например:

```
                    SOURCE OF TRUTH
Профиль мастера ───────────► beautygo_backendУслуги салона ─────────────► beautygo_backendНазначения услуг ──────────► beautygo_backendРабочий график ────────────► beautygo_backendЗаписи Ayla ───────────────► beautygo_backendСтатус записи ─────────────► beautygo_backendОплата ────────────────────► beautygo_backend
Разговор ──────────────────► ai-bot-platformAI context ────────────────► ai-bot-platformAssistant runtime ─────────► ai-bot-platform
```

Иначе через несколько месяцев обнаружим `Appointment` в одном репозитории и его немного отличающуюся копию в другом.

---

# 5. Не менее важно — кто имеет право что делать

Не просто:

> «Есть экран расписания».

А:

| Действие | Master | Admin | Owner | Ayla |
| --- | --- | --- | --- | --- |
| Смотреть своё расписание | ✅ | ✅ | ✅ | read |
| Смотреть всех мастеров | ❌ | ✅ | ✅ | — |
| Изменить свой график | ? | ✅ | ✅ | propose? |
| Создать time-off | ? | ✅ | ✅ | propose? |
| Создать запись | ✅? | ✅ | ✅ | client flow |
| Отменить запись | ✅ | ✅ | ✅ | command |
| Назначить услугу | ❌ | ✅ | ✅ | ❌ |
| Удалить мастера | ❌ | ? | ✅ | ❌ |
| Изменить цену | ❌/? | ✅ | ✅ | ❌ |

Знаки `?` надо решить **сейчас**, до макетов.

Потому что permission model уже существует в backend (`staff/admin`, `IsTenantAdmin`, `IsSpecialist`).

---

# 6. Нужно разделить Query и Command

Это сильно уменьшит будущий хаос.

Экран **не должен собирать себя пятьюдесятью запросами к CRUD API**.

Например экран владельца «Сегодня» не должен делать:

```
GET mastersGET working-hours × 5GET appointments × 5GET services × 20GET time-off × 5...
```

Нужна screen-oriented projection:

```
GET /pro/operations/today?date=2026-08-14
```

которая возвращает готовое представление:

```JSON
{  "date": "...",  "masters": [    {      "master": {},      "working_state": "working",      "appointments": [],      "blocks": []    }  ]}
```

А **изменения** идут через domain commands:

```
POST appointmentPOST appointment/{id}/reschedulePOST appointment/{id}/cancelPOST appointment/{id}/completePOST time-offPATCH working-hours
```

Это классическая полезная идея из CQRS, но нам не нужен настоящий сложный CQRS. Просто:

> **читать удобно для экрана, писать правильно для домена.**

---

# 7. Надо продумать не только happy path

На каждый основной экран нужны состояния:

```
LoadingEmptyNormalPartial dataErrorOffline/timeoutPermission deniedConflict/stale data
```

Например администратор открыл запись 10:00.

Пока он её переносил, мастер уже перенёс её с телефона.

Backend у нас уже имеет optimistic version для reschedule.

UX поэтому должен уметь сказать:

> Запись уже была изменена Анной.  
> Сейчас она назначена на 12:00.

**[Показать актуальные данные]**

А не просто «Ошибка 409».

---

# 8. Отдельно надо определить роль Ayla

Иначе здесь возникнет самый большой архитектурный бардак.

Для каждого действия ставим один из четырёх режимов:

```
Ayla can READAyla can SUGGESTAyla can EXECUTE WITH CONFIRMATIONAyla CANNOT ACCESS
```

Например:

```
"Какие у меня сегодня клиенты?"READ
"У меня завтра не получится работать"SUGGEST:"Закрыть завтра с 10 до 19?"
[Подтвердить]
EXECUTE WITH CONFIRMATION
```

Но:

> «Удали Ирину из салона»

Ayla не должна просто выполнить destructive operation из разговора.

Так появляется **Ayla Action Matrix**.

---

# Что нам сейчас реально нужно создать

Перед разработкой я бы сделал **один короткий canonical пакет**, а не ещё 15 handoff'ов:

```
Salon Operations MVP│├── 01 Roles & Permissions│├── 02 Domain Model│├── 03 Screen Map│├── 04 Screen Data Contracts│├── 05 User Flows│├── 06 State Transitions│├── 07 Ayla Action Matrix│├── 08 API Mapping│└── 09 Acceptance Scenarios
```

И центральной таблицей сделать:

| Screen | Role | Reads | Commands | Backend | Ayla |
| --- | --- | --- | --- | --- | --- |
| Today | Master | appointments, hours | complete/no-show | appointments | briefing |
| Schedule | Master | availability | time-off | appointments | suggest |
| Appointment | Master | appointment/customer | cancel/reschedule | appointments | assist |
| Today | Admin | all schedules | booking actions | operations projection | alerts |
| Team | Admin | staff | invite/deactivate | users/tenants | — |
| Services | Admin | salon services | assign master | services | — |
| Master Detail | Admin | profile/services/hours | edit | several domains | — |

**Вот эта таблица должна стать мостом между Figma, frontend и backend.**

И только после неё рисовать окончательные макеты. Тогда дизайнер знает, какие данные существуют; frontend знает контракт; backend знает projection; QA знает expected state; а Ayla знает, что ей разрешено читать и менять.

Это, на мой взгляд, **самая важная работа перед недельным рывком**. Сам код у нас сейчас менее опасен, чем отсутствие такой общей карты.