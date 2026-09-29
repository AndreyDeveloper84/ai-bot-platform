# DRF-1297 — второй проход. Проверка четырёх архитектурных решений

> Аудит read-only. В коде, в Linear и в базах пилота ничего не менялось.
>
> **База сверки:** `AndreyDeveloper84/beautygo_backend`, ветка `dev`, коммит
> `e07388ffe21e3a5c339229f70b84cccf234d2e49`. Перепроверено при старте второго
> прохода: `origin/dev` не сдвинулся с первого прохода, аудит актуален.
> Бот выложен на `0ff2494`, но салонная поверхность живёт в этом репозитории и
> изменений не получала.
>
> Первый проход сохранён ниже, под разделителем. Здесь — только то, что
> проверялось во втором.

---

## 1. Итоговый вердикт

# READY WITH GAPS

Изменение относительно первого прохода (`NOT READY`) — не смягчение, а результат
проверки. В первом проходе блокировало не количество пробелов, а **неизвестность
архитектуры**: было непонятно, каким способом Ayla вообще попадает в салон и можно
ли сохранить различие прав. Оба вопроса теперь закрыты кодом, и ответы хорошие.

| Решение | Статус | Стоимость |
|---|---|---|
| **В-1** read/write путь Ayla | **PARTIAL** | write-путь **уже построен и выложен**; read требует отдельного токена и разделения четырёх смешанных классов |
| **В-2** права в Controlled Pilot | **SUPPORTED** | машинерия существует (`appointments/authz.py`), нужно ~7 строк на действие |
| **В-3** перенос без смены мастера | **SUPPORTED** | менять нечего, уже запрещено конструкцией; правка только в тексте Linear |
| **В-4** Conflict Guard на сокращение доступности | **PARTIALLY** | две операции из четырёх закрываются дёшево, две требуют отдельной работы |

Три из четырёх решений владельца проходят по коду, и два из них — **без единой
строки backend-изменений**.

**Что по-прежнему не поддержано и не сдвинулось с первого прохода — ровно два
пункта:**

1. **Слой «Изменения» / «Требует внимания»** — read-API событий нет ни одного,
   маркера «последний просмотр» нет, объекта обращения нет, изменения графика
   событий не порождают, а атрибуция салонных действий сломана (P0-J первого
   прохода — событие отмены говорит `actor: "user"`).
2. **Поиск свободного времени** — салонной ручки доступности нет, многомастерного
   поиска нет структурно.

**Условие заморозки.** DRF-1249 можно замораживать целиком, **кроме** этих двух
поверхностей. Они либо получают backend, либо явно выводятся из P0 — и тогда
макет не должен их обещать. Всё остальное к рисованию готово, и границы теперь
точные.

---

## 2. Решения В-1 — В-4
### В-1. Read path и write path Ayla — **PARTIAL, но лучше, чем ожидалось**

#### Write half: целевая архитектура уже существует в коде

Владелец описал write-путь так: сервисный credential доказывает, что звонит Ayla,
а действие исполняется **с actor'ом человека**. Это дословно то, что делает
`IsBotServiceWithVerifiedClient` (`users/permissions.py:134-207`):

1. `Authorization: Bearer` сверяется с `AYLA_INTERNAL_API_TOKEN` через
   `compare_digest`, пустое значение fail-closed (`:181-192`);
2. `X-External-User-ID` резолвится в живого пользователя Ayla (`:194-200`);
3. **`request.user` подменяется на этого человека** (`:206`);
4. следом `IsTenantAdmin` доказывает, что **этот человек** вправе действовать
   **в этом салоне** (`tenants/appointments_api.py:137-140`).

Сервисный credential при этом сотрудником салона не становится: он доказывает
только факт звонка, а полномочие приходит от человека. Атрибуция дальше идёт от
`request.user.id` (`tenants/appointments_api.py:509`, `:578`).

**Вывод: строить write-путь не нужно, он выложен.** Никаких изменений permissions
для write не требуется.

#### Но сегодня на дальнем конце `X-External-User-ID` не может оказаться администратор

Это не теория, это цепочка из трёх мест кода.

`resolve_external_user` (`users/services.py:92-127`) принимает строку строго по
регулярке `^[a-z][a-z0-9_-]*(?::[A-Za-z0-9_-]{1,64})+$` (`users/services.py:69`) и
делает `get_or_create(username=external_user_id, defaults={"role": "client",
"is_proxy": True})` (`:124-127`).

Отсюда:

* назвать администратора его телефоном нельзя — `+79001234567` регулярку не
  проходит (нет двоеточия, начинается не с строчной буквы). А `provision_salon_admin`
  создаёт учётку именно с `username=phone` (`users/management/commands/provision_salon_admin.py:69`);
* значит `bot:telegram:<id>` резолвится в **свежий proxy с `role="client"`**, у
  которого нет никакой связи с тенантом → `IsTenantAdmin` даёт 403.

Единственный мост — `bind_external_identity` (`users/services.py:168`), который
ставит `linked_user` и заставляет резолвер вернуть настоящий аккаунт. Но у него
есть ограничение (`users/services.py:185-189`):

> Target must exist and be a REAL, active, non-soft-deleted **CLIENT** account
> (`is_proxy=False`, `role='client'` — no proxy→proxy chains, **no binding to
> staff/admin accounts**).

#### Лазейка существует, и она узкая — но её достаточно для P0

`IsTenantAdmin` **не смотрит на `User.role` вообще**. Он проверяет только строку
`TenantUserRelationship` (`users/permissions.py:339-344`):

```python
return TenantUserRelationship.objects.filter(
    user=user, tenant=request_tenant,
    role=TenantUserRelationship.Role.ADMIN, is_active=True,
).exists()
```

А `provision_salon_admin` в ветке «аккаунт уже существует» **сохраняет прежнюю роль
пользователя** и выдаёт полномочие только связью
(`provision_salon_admin.py:75-79`, комментарий там прямо говорит: «a client who also
administers a salon is a normal situation, and the grant below is what actually
authorises them»).

Значит рабочая комбинация такая:

| `User.role` | связь с тенантом | `bind_external_identity` | `IsTenantAdmin` | итог |
|---|---|---|---|---|
| `admin` (создан командой с нуля) | admin | **отказ** — binding к admin-аккаунту запрещён | прошёл бы | **Ayla писать не может** |
| `client` (аккаунт был раньше, повышен связью) | admin | **разрешён** | **проходит** | **Ayla пишет от имени человека** |

То есть решение чисто операционное: пилотный администратор должен быть заведён как
обычный клиентский аккаунт и повышен **связью**, а не создан командой с
`role="admin"`. Кода менять не нужно.

Второе условие: `bind_external_identity` доступен только под отдельным токеном
`AYLA_IDENTITY_PROVISIONING_TOKEN`, который «never deployed to the bot service»
(`users/permissions.py:262-264`), и его docstring прямо говорит, что
bot-driven binding в проде не поддержан. Для одного пилотного администратора это
не блокер — ровно этот сценарий («trusted provisioning / E2E bootstrap / ops
only», `users/permissions.py:281-282`) токен и обслуживает. Разовая операция ops.

#### Read half: главный риск — смешанные по методам классы

Permissions в DRF задаются **на класс, а не на метод**. Ни одного места с
пометодной гранулярностью в дереве нет: `get_permissions` и
`has_object_permission` не встречаются ни разу. Поэтому «открыть на чтение» класс,
который умеет и писать, значит открыть и запись.

| Поверхность | Класс | Методы | Открывать read-only? |
|---|---|---|---|
| журнал дня | `TenantDayView`, `tenants/day_api.py:35` | только `get` (`:66`) | **безопасно** |
| поиск клиента | `SalonCustomerLookupView`, `tenants/appointments_api.py:251` | только `get` (`:286`) | **безопасно** |
| недельный график | `AdminScheduleView`, `users/schedule_admin_api.py:81` | `get`+`put`+`patch` (`:84-91`) | **опасно** |
| исключения даты | `AdminScheduleExceptionListView`, `users/schedule_admin_api.py:381` | `get`+`put` (`:401`, `:426`) | **опасно** |
| недоступность | `AdminTimeOffListView`, `users/schedule_admin_api.py:220` | `get`+`post` (`:235`, `:238`) | **опасно** |
| закрытия салона | `TenantClosureListView`, `users/schedule_admin_api.py:520` | `get`+`post` (`:541`, `:561`) | **опасно** |
| impact preview | `AdminScheduleImpactView`, `users/schedule_admin_api.py:183` | только `get` (`:203`) | **безопасно** |

Итого: **из семи салонных read-поверхностей три безопасны как есть, четыре —
смешанные классы**, и открыть их сервисному credential нельзя, не открыв ему же
PUT/POST/PATCH. Это и есть точный ответ на вопрос владельца №4.

#### Второй риск: единый секрет

`IsInternalBearer` и `IsBotServiceWithVerifiedClient` читают **один и тот же**
`AYLA_INTERNAL_API_TOKEN` (`users/permissions.py:181`, `:241`;
`djangoProject/settings/base.py:672`). Если read-доступ Ayla выдать под этим же
токеном, «read-only credential» получится только на бумаге: его же предъявитель
уже имеет write-поверхность.

В репозитории уже есть **готовый образец решения** — `IsIdentityProvisioningBearer`
(`users/permissions.py:254-305`) с отдельным секретом, с явным отказом принимать
общий токен и с системной проверкой `users.E001`, падающей на старте, если два
секрета совпали (`:272-277`). Read-only credential для Ayla должен повторить
ровно этот приём, а не переиспользовать общий токен.


#### Третий риск, и он самый серьёзный: сегодняшний сервисный доступ не скоуплен по тенанту

Это то, чего я не проверил в первом проходе, и оно меняет форму решения.

`TenantContextMiddleware` исключает всё поддерево `/api/v1/internal/`
(`users/middleware.py:226-242`), поэтому там `request.tenant` **всегда `None`**
(`:263`, ранний выход `:266-267`). А `IsTenantAdmin` при `tenant is None` fail-closed
(`users/permissions.py:335-337`). Из этого следует жёсткая развилка, которую надо
назвать прямо:

> **Все read-ручки, скоупленные по тенанту, доступны только человеку по JWT.
> Все read-ручки, доступные сервисному токену, по тенанту не скоуплены.**

Проверено поимённо:

| Ручка под `IsInternalBearer` | Скоуп | Что видно держателю токена |
|---|---|---|
| `GET /api/v1/internal/specialists/…` (`users/internal_catalog_api.py:30`) | **никакого** — queryset `users/specialists_api.py:432-436` фильтрует только `status=ACTIVE`, в `SpecialistFilter` (`:310-335`) поля тенанта нет | **все активные мастера платформы** |
| `GET /api/v1/internal/catalog/salon-services/` (`services/internal_api.py:43`) | `tenant` — **необязательный query-параметр** (`:50`), не скоуп | каталоги всех салонов |
| `GET /api/v1/internal/catalog/specialist-services/` (`services/internal_api.py:58`) | то же (`:68`) | связки всех салонов, плюс `yclients_staff_id` и `user_id` (`services/serializers.py:204-211`) |
| `GET /api/v1/internal/services/`, `/categories/` (`services/internal_api.py:24`, `:33`) | нет | весь каталог |
| `GET /api/v1/internal/users/{user_id}/` (`users/internal_users_api.py:105`) | **нет ни скоупа, ни проверки связи** (`:146-150`) | любой user id → `display_name` + `avatar_url` |

Контрпримеры, где сделано правильно, — и оба скоуплены **полем в теле**, а не
`request.tenant`: `users/masters_internal_api.py:143` и
`users/internal_schedule_api.py:118-122`.

**Вывод для архитектуры.** Выдать Ayla read-credential на существующее внутреннее
дерево — значит выдать межарендаторное чтение. Для пилота с шестью арендаторами это
уже не абстракция. Правильная форма read-доступа Ayla — **не** «пустить сервисный
токен во внутреннее дерево», а **пустить его на `/api/v1/tenants/me/…`**, где
`request.tenant` резолвится из `X-Tenant` (`users/middleware.py:269-296`) и все
querysets уже скоуплены. Это ровно то, что `tenants/day_api.py:1-9` объясняет как
причину, по которой журнал дня живёт на тенантном маршруте, а не на внутреннем.

То есть read-credential должен быть устроен так же, как write-credential: **токен
доказывает звонок, `X-Tenant` задаёт салон, скоуп берётся из middleware.**

#### Ответ на вопрос владельца №6: различить двух одноимённых клиентов

Маскированного телефона backend не отдаёт, и **переиспользовать нечего**: хелпера
маскирования в дереве нет ни одного. Единственный частичный вывод в кодовой базе —
`last4` банковской карты (`payments/models.py:161`), к телефону отношения не имеет.
`ai/redaction.py:37-49` делает полное удаление (`[PHONE]`), а не маскирование, и
применяется только к тексту перед отправкой в OpenAI (`ai/application/services/chat_service.py:100`).

Зато есть **два различителя, которые уже лежат в базе и просто не сериализуются**:

1. **`TenantUserRelationship.granted_at`** (`users/models.py:732`, `auto_now_add`) —
   «клиент этого салона с такого-то числа». Уже скоуплен по тенанту, и строка уже
   загружается через `select_related("user")` в самом поиске
   (`tenants/appointments_api.py:319`). Добавление — одно поле в словарь на
   `:327-330`.
2. **Дата последнего визита / число визитов** — денормализованного поля нет
   (проверено: `last_visit`, `visit_count`, `total_visits` в дереве отсутствуют), но
   агрегат по `Appointment` в границах того же тенанта считается.

Полей даты рождения в системе нет вовсе — это подтверждается тем, что три
единственных упоминания `birth` в дереве описывают, чего именно не отдаётся
(`users/internal_users_api.py:18`, `:141`).

**Вывод: безопасное различение достижимо без раскрытия телефона и без нового
маскирования.** Самый дешёвый ход — отдавать `granted_at` в поиске клиента.

#### Минимальное изменение для P0 — итог по В-1

1. **Отдельный секрет** `AYLA_READONLY_API_TOKEN` и permission-класс по образцу
   `IsIdentityProvisioningBearer` (`users/permissions.py:254-305`), включая отказ
   принимать общий токен и системный чек, падающий на старте при совпадении
   секретов (`:272-277`). Переиспользовать `AYLA_INTERNAL_API_TOKEN` нельзя: его
   предъявитель уже имеет write-поверхность.
2. **Ставить read-доступ на `/api/v1/tenants/me/…`, а не на `/api/v1/internal/…`** —
   там `request.tenant` резолвится middleware и querysets уже скоуплены. Внутреннее
   дерево для этого не годится: оно исключено из tenant-middleware и его read-ручки
   отдают данные всех арендаторов.
3. **Три класса подключаются как есть** — они GET-only: `TenantDayView`,
   `SalonCustomerLookupView`, `AdminScheduleImpactView`.
4. **Четыре смешанных класса** (`AdminScheduleView`, `AdminScheduleExceptionListView`,
   `AdminTimeOffListView`, `TenantClosureListView`) — вынести GET в отдельные
   read-only view с теми же querysets и сериализаторами. Это не новая read API, а
   разделение по методам. Иначе read-credential неизбежно получит запись.
5. **Операционно, без кода:** завести пилотного администратора как клиентский
   аккаунт, повысить его связью `role=admin`, один раз связать бот-идентичность
   через `bind_external_identity` под провижининг-токеном.
6. **Дёшево и полезно:** отдавать `granted_at` в поиске клиента — строка уже
   загружена, различение двух «Анн» перестаёт быть невозможным.

Ничего из этого не требует новой доменной логики.
### В-2. Права в Controlled Pilot — **SUPPORTED. Я ошибся в первом проходе.**

> **Исправление первого прохода.** В P0-E я написал, что backend различить право
> видеть и право действовать «не может», и что «гранулярности нет вообще».
> Основанием был поиск по `has_object_permission` и `get_permissions` — их
> действительно нет ни одного. **Вывод был неверен.** Гранулярность в кодовой базе
> есть, просто она реализована не механикой DRF, а отдельным модулем.

#### Что реально существует: `appointments/authz.py`

Модуль на 106 строк, целиком посвящённый вопросу «в каком качестве этот вызывающий
действует на **этой** строке».

| Функция | Строки | Что делает |
|---|---|---|
| `has_tenant_admin_grant(request)` | `appointments/authz.py:40-61` | активная связь `role=ADMIN` в адресованном тенанте; зеркалит `IsTenantAdmin`, но вызывается **внутри действия**, когда permission-машинерия DRF уже отработала (`:43-46`) |
| `may_operate_on_bookings(request)` | `:64-75` | дешёвый pre-lock гейт, **намеренно row-independent** — отсекает клиента и гостя до взятия блокировки строки |
| `resolve_booking_operator(request, appointment)` | `:78-106` | **row-aware**: возвращает `SPECIALIST`, `SALON` или `None`; `None` обязан рендериться как 404, а не 403 (`:81-82`) |

Это ровно то различие, которое владелец требует сохранить: право по объекту
(`appointment.tenant_id == request_tenant.id`, `:101`) отделено от роли
(`has_tenant_admin_grant`, `:102`), и оба отделены от классового permission.

Особенно показательна строка `:92-96`: специалист, у которого **есть также
админский грант**, на своей записи резолвится как `SPECIALIST`, а на записи
коллеги — как `SALON`. Один и тот же человек, одно и то же действие, разный actor
в зависимости от объекта. Модуль это фиксирует в docstring (`:28-29`).

#### Где эта машинерия применена, а где нет

```
appointments/views.py:434  may_operate_on_bookings   (complete)
appointments/views.py:479  resolve_booking_operator  (complete)
appointments/views.py:566  may_operate_on_bookings   (no_show)
appointments/views.py:596  resolve_booking_operator  (no_show)
tenants/appointments_api.py:709  resolve_booking_operator  (салонное завершение визита)
```

И это **весь** список вызовов вне тестов.

То есть: `complete` и `no_show` проходят через per-action, per-object проверку,
а салонные `create` / `reschedule` / `cancel` — нет. Они опираются только на
классовый список `_SALON_WRITE_PERMISSIONS` (`tenants/appointments_api.py:137-140`),
одинаковый для всех трёх. Управление графиком — тоже один список на все шесть
классов (`users/schedule_admin_api.py:44-48`).

#### Ответы на вопросы блока 2

1. **Чтение appointment.** `AppointmentViewSet.get_queryset` фильтрует по тенанту
   (`appointments/views.py:126-128`), затем клиент → свои (`:130-131`), мастер →
   свои (`:132-133`), **иначе `qs.none()`** (`:134`). Утверждение эпика DRF-1063
   подтверждено: администратор в этой ручке не видит ничего. Салон читает записи
   через отдельную проекцию `GET /api/v1/tenants/me/day/`.
2. **create / reschedule / cancel.** Только классовый гейт, per-action различия нет.
3. **Изменение графика.** Только классовый гейт `_ADMIN_PERMISSIONS`
   (`IsAuthenticated + IsProApp + IsTenantAdminOrPlatformAdmin`).
4. **Различие object / action permission — есть**, в `appointments/authz.py`,
   применено к двум действиям из шести.
5. **Роли.** `User.ROLE_CHOICES` = `client | specialist | admin`
   (`users/models.py:11-16`). `TenantUserRelationship.Role` =
   `customer | staff | admin` (`users/models.py:700-703`). Полномочие в салоне
   даёт **только** связь: `IsTenantAdmin` смотрит исключительно на неё и не
   читает `User.role` вовсе (`users/permissions.py:339-344`).
6. **Где определяется Staff/Admin/Owner.** Фактически — нигде, кроме
   `TenantUserRelationship.role`. `Role.STAFF` в авторизации не читается ни разу.
   Понятия Owner отдельно от Admin в коде нет.
7. **Что будет, если выдать одному пилотному пользователю весь набор P0-действий.**
   Формально — ничего не произойдёт, потому что **набора нет**: `role` это скаляр
   с тремя значениями (`users/models.py:723-724`), а не множество разрешений. Полей
   capability/scope на связи нет. Выдача «всего набора» сегодня выражается ровно
   одной строкой `role=admin, is_active=True` — что и делает
   `provision_salon_admin.py:121-127`. Это **не создаёт** правила «кто видит, тот
   и меняет»: правило не появится, пока действия проверяются по-разному, а место
   для разной проверки уже есть.

#### Вывод

**Новая permission-машинерия для P0 не нужна.** Достаточно расширить
`appointments/authz.py` функциями уровня действия (`may_cancel`, `may_reschedule`,
`may_change_schedule`) и вызывать их внутри соответствующих действий — ровно так,
как `complete` уже вызывает `resolve_booking_operator`. Это добавляет **семь строк
на действие**, не трогает модели, не требует редактора ролей и сохраняет
архитектурное различие, которого требует владелец.

Пилотному администратору при этом все проверки будут отвечать «да» — что владелец
и разрешил. Различие остаётся **в коде**, а не в UI.

Privacy клиента остаётся отдельной data-access policy и с операционными правами не
смешивается: телефон не отдаётся ни одной салонной ручкой независимо от роли.
### В-3. Смена мастера при переносе — **SUPPORTED. Запрещать нечего: уже запрещено конструкцией.**

#### Довод владельца про цену подтверждён, довод про длительность — наполовину

`SpecialistService` (`services/models.py:402-465`) — связка «мастер ↔ салонная услуга»:

| Поле | Строка | Своё или наследуется |
|---|---|---|
| `price` Decimal, `MinValueValidator(1)` | `services/models.py:434` | **всегда своё**, `NOT NULL`, фолбэка нет |
| `buffer_after_minutes` | `:439` | **всегда своё** |
| `duration_minutes` | `:430` | **nullable**, каскад |
| `is_active` | `:440` | своё, влияет на eligibility |
| `requires_health_check` | `:438` | своё |

Уникальность объявлена на **паре** `(specialist, salon_service)`
(`services/models.py:446-449`), то есть N мастеров на одну салонную услугу — N строк,
у каждой своя обязательная цена.

Единственный резолвер эффективных условий — `resolve_bookable_service`
(`services/service_resolver.py:59`), и его docstring прямо запрещает второй путь
резолва (`:13`). Салонная ветка (`:103-135`):

```python
duration = (salon.duration_minutes
            if salon.duration_minutes is not None
            else link.resolved_duration())
return ResolvedService(..., duration_minutes=duration,
                       price=link.price,
                       buffer_after_minutes=link.buffer_after_minutes)
```

**Уточнение к формулировке владельца.** Цена и буфер — да, всегда персональные
(`:133-134`). А длительность при бронировании берётся из **салонной** строки, и
персональная длительность мастера используется только если салонная `NULL`
(`:123-127`). Это инвертирует каскад самой модели `resolved_duration()`
(`services/models.py:466-478`) — намеренно, по AMD-019.

Для аргумента это не важно: **цены достаточно**. Смена мастера меняет стоимость —
значит это коммерческий выбор, а не правка времени. Но в формулировке для Linear
не стоит писать «у каждого мастера своя длительность» как плоский факт: на пилоте
у всех 58 услуг салонная длительность задана, и она победит.

#### Что перенос делает на самом деле

`RescheduleBookingDTO` (`appointments/application/dto.py:76-108`) несёт восемь полей:
`booking_id`, `initiator_user_id`, `new_start_at`, `initiator_role`,
`expected_version`, `tenant_id`, `command_key`, `basis`. **Поля мастера нет.**

Все четыре HTTP-поверхности переноса собирают DTO поимённо, без `**request.data`:

| Поверхность | Сериализатор | Принимает |
|---|---|---|
| мобильная/pro | `appointments/serializers.py:167-174` | `new_start_datetime`, `expected_version` |
| внутренняя/бот | `appointments/serializers.py:177-187` | то же, версия обязательна |
| салонная консоль | `tenants/appointments_api.py:191-202` | то же, версия обязательна |
| AI action service | `ai/application/services/action_service.py:105-170` | действия переноса **не существует вовсе** |

Внутри сервиса мастер берётся **из самой записи**, а не от вызывающего:
`specialist_id=appointment.specialist_id` (`cancel_reschedule_service.py:306`), с
комментарием `:301-305`: «specialist_id is immutable on an Appointment».

Запись ограничена жёстким allowlist из четырёх полей
(`cancel_reschedule_service.py:451-453`):

```python
appointment.save(update_fields=[
    "start_datetime", "end_datetime", "version", "updated_at",
])
```

Длительность переносится как `timedelta` со старой строки
(`:410-414`), снимки цены/комиссии/дохода не пересчитываются вообще, а событие
жёстко объявляет `changed_fields = ["starts_at"]` (`:503`).

`AppointmentRevision` (`appointments/models.py:403-470`) физически не имеет поля,
куда смену мастера можно было бы записать.

#### Единственное место, где мастера всё-таки можно подменить

Django-админка: `appointments/admin.py:41` держит `specialist`, `service` и `price`
**редактируемыми**, тогда как все семь `snapshot_*` вынесены в `readonly_fields`
(`:27-33`). Суперпользователь может переставить мастера, и снимки останутся от
прежнего. Это тот же класс опасности, что уже зафиксирован прямым запретом в
эпике DRF-1063 («Django-админка не является заменой и опасна»).

#### Прецедент продукта — уже в коде

При увольнении мастера продукт уже отказался переназначать записи
(`users/services.py:1274-1280`):

> Cancellation is the only supported action. Reassigning to another master is out
> of scope (it moves money: snapshot price, specialist income and the service XOR
> constraint all have to be answered first), and "offer a reschedule" was
> deliberately replaced by cancel-then-offer

Реализация следует за словами: вызывается `CancelBookingService`
(`users/services.py:1336-1344`). То есть предложение владельца «нужен другой мастер
→ новая запись + отдельная отмена» **совпадает с уже принятым решением** в другом
месте системы.

#### Один настоящий пробел

Инвариант держится комментариями, allowlist'ом и составом DTO — но **ни одним
тестом**. Ни один из пяти наборов тестов переноса не читает `specialist_id` после
операции; `test_snapshot_is_immutable` (`appointments/tests/test_domain.py:197`)
проверяет неизменяемость frozen-dataclass, а не строки в базе. Добавь кто-нибудь
поле в DTO или расширь `update_fields` — не упадёт ничего.

#### Побочная находка, важная для UX переноса

Салонный перенос **намеренно не проверяет рабочие часы и закрытия салона**:

```python
# cancel_reschedule_service.py:428-433
apply_common_booking_guards(
    specialist_id, new_interval, self._booking_window_policy,
    enforce_schedule=(initiator_role == "client"),
)
```

Салон передаёт `initiator_role="salon"` (`tenants/appointments_api.py:511`), значит
`enforce_schedule=False`, и `check_schedule_frame` / `check_tenant_closure`
пропускаются (`_booking_guards.py:213-215`). Это осознанное решение DRF-1062,
объяснённое прямо в коде (`cancel_reschedule_service.py:424-427`): «refusing it
because the weekly template ends at 19:00 is the same "system says no" dead end
this task removes».

**Недоступность (TimeOff) при этом не пропускается никогда**
(`_booking_guards.py:203-207`, `:216`) — вычитается всегда, независимо от актора.

Для UX это значит: салон **вправе** перенести запись за пределы рабочего дня
мастера, и это не ошибка. Формулировка DRF-1239 «новые мастер/время берутся только
из authoritative availability» это состояние не описывает.

**Итог: SUPPORTED.** Правка нужна не в backend, а в тексте DRF-1237/1239.
### В-4. Conflict Guard на любое сокращение доступности — **PARTIALLY**

Проверял предложение «полный preview остаётся на недоступности, а недельный
шаблон, исключение даты и закрытие салона получают жёсткий 409, переиспользуя
существующую проверку».

**Направление верное, но посылка «переиспользовать существующую проверку» неточна
в двух местах, и второе меняет объём работ.**

#### Неточность первая: переиспользовать нечего — общего хелпера не существует

Проверка в ручке недоступности это не функция, а **инлайновый queryset**
(`users/schedule_api.py:417-431`):

```python
active_count = Appointment.objects.filter(
    specialist=specialist,
    status__in=[s.value for s in ACTIVE_BOOKING_STATUSES],
    start_datetime__lt=end_at,
    end_datetime__gt=start_at,
).count()
if active_count > 0:
    return error_response("HAS_ACTIVE_APPOINTMENTS", ..., status_code=409)
```

Тот же текст **уже скопирован дважды**: `users/internal_schedule_api.py:134-147` и,
в форме исключения, `users/schedule_admin_api.py:278-287`. Проверено по всем
местам, где вообще произносится этот код ошибки — три независимых сайта эмиссии:
`users/schedule_api.py:428`, `users/internal_schedule_api.py:143`,
`users/schedule_admin_api.py:280`.

То есть собственный прецедент кодовой базы для «переиспользования» — это копипаста
шести строк, а не вызов. Первым ходом надо **извлечь хелпер**. Он тривиален и
правилен, но это работа, а не «уже есть».

Отдельно: проверка идёт **вне транзакции и без блокировки** — запись может
появиться между `.count()` и `SpecialistTimeOff.objects.create(...)`
(`users/schedule_api.py:433-438`). Дешёвый 409 остаётся TOCTOU-открытым. Для P0 это
приемлемо — он ловит человеческую ошибку, а не гонку, — но гарантией называть
нельзя.

#### Неточность вторая, меняющая объём: сокращение задаёт другой вопрос

`get_schedule_impact` (`appointments/application/services/schedule_impact_service.py:94-98`)
берёт **произвольное окно** — привязки к `SpecialistTimeOff` в сигнатуре и теле нет:

```python
def get_schedule_impact(specialist, start_at: datetime, end_at: datetime) -> ScheduleImpact:
```

Он отвечает на вопрос **«какие живые записи попадают внутрь этого окна»**
(`:106-117`, фильтр `ACTIVE_BOOKING_STATUSES` —
`appointments/domain/value_objects.py:109-113`).

Сокращение графика задаёт вопрос **«какие живые записи оказываются снаружи
предлагаемой рамки»**. Это не то же самое, и вот почему это не педантизм:

**Записи снаружи рабочих часов существуют законно.**
`appointments/application/services/create_booking_service.py:253-259`:

```python
if dto.actor_role == "user":
    check_schedule_frame(dto.specialist_id, target_interval)
    check_tenant_closure(dto.specialist_id, target_interval)
```

Рамка проверяется **только для клиентских записей**. Walk-in мастера и салонная
запись создаются вне рабочих часов намеренно — закреплено тестом
`appointments/tests/test_schedule_frame_holes_1062.py:400-416`. Салонный перенос
тоже (`cancel_reschedule_service.py:428-433`).

Отсюда: наивный guard «всё, что вне новой рамки — конфликт» будет ругаться на
записи, которые **и в старой рамке были снаружи** и никем не вытеснены. Чтобы
ответить правильно, нужен диф старой и новой рамки.

**Функции, которая считает «какие записи выпадают из предлагаемой рамки», в дереве
нет ни одной.** Ближайшее — `check_schedule_frame`
(`appointments/application/services/_booking_guards.py:66-165`), но он обратного
направления: берёт один интервал и спрашивает, помещается ли тот в **уже
сохранённую** рамку, и бросает исключение вместо возврата списка.

#### Таблица по шести операциям

| # | Операция | Endpoint | Сервис | Проверка сейчас | При пересечении | 409 | preview | impact_token | Переиспользование | Минимальное изменение |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Создание/расширение частичной недоступности | `POST /tenants/me/masters/{id}/time-off/` | `TimeOffListView.post` → `apply_absence_with_resolutions` (`users/services.py:1250-1378`) | **есть**, инлайн (`schedule_api.py:417-431`) + полный preview | 409, либо атомарная отмена по решениям | **да** | **да** (`schedule_admin_api.py:183-217`) | **да** (`schedule_impact_service.py:73-91`) | — | ничего, это эталон |
| 2 | Сокращение недельных часов | `PUT/PATCH /tenants/me/masters/{id}/schedule/` | `ScheduleView.put/patch` (`schedule_api.py:257-338`) | **нет** | молча 200, запись осиротела | нет | нет | нет | **NO** | новая реализация |
| 3 | Недельный день → «не работает» | те же | те же | **нет** | молча 200 | нет | нет | нет | **NO** | новая реализация |
| 4 | Исключение даты с сокращением часов | `PUT .../schedule-exceptions/` | `AdminScheduleExceptionListView.put` (`schedule_admin_api.py:426-452`) | **нет** | молча 200 | нет | нет | нет | **PARTIALLY** | два окна вместо одного + диф старой рамки |
| 5 | Исключение даты «не работаю» | те же (`is_working_day=false`, `:439`) | те же | **нет** | молча 200 | нет | нет | нет | **YES** | ~6 строк + конверсия даты в UTC |
| 6 | Закрытие салона | `POST /tenants/me/closures/` | `TenantClosureListView.post` (`schedule_admin_api.py:561-586`) | **нет** — 409 там означает «закрытие уже есть» | молча 201 | нет | нет | нет | **PARTIALLY** | цикл по мастерам + конверсия TZ на каждого |

#### Почему №2 и №3 — новая реализация, а не 409

Недельное изменение **не имеет даты**: `PUT` удаляет и пересоздаёт все семь строк
(`schedule_api.py:270-282`), `PATCH` апсертит по дням недели (`:316-327`). Чтобы
найти конфликты, изменение надо развернуть по будущим датам.

Горизонт для этого есть и уже применяется здесь же: `_invalidate_slots` зовётся с
`today → today + BOOKING_MAX_AHEAD_DAYS` (`schedule_api.py:284-286`, `:329-331`;
`BOOKING_MAX_AHEAD_DAYS = 60`, `djangoProject/settings/base.py:379`). Горизонт
обоснован: `validate_booking_window` режет создание дальше 60 дней безусловно
(`appointments/domain/policies.py:273-276`, вызов `create_booking_service.py:148`).

Но «переиспользование» здесь означает: до 60 вычислений дифа рамки по датам, каждое
поверх несуществующей функции дифа, плюс ловушка walk-in на каждой из 60 дат.

Вдобавок `ScheduleView.put` **не обёрнут в транзакцию и не берёт блокировку**
(`:270-282`) — любой guard, прикрученный сюда, TOCTOU-открыт по построению.

#### №6: препятствие не блокирующее, но реальное

`TenantClosureListView` **не наследует** `_TenantScopedSpecialistMixin`
(`schedule_admin_api.py:520`, `:529`) — специалиста в области видимости нет вообще.
И `Tenant` не имеет таймзоны, поэтому локальное окно закрытия резолвится в **разное
UTC-окно для каждого мастера**. Логика такой конверсии в дереве есть
(`appointments/infrastructure/availability/providers.py:141-158`, с DST-безопасной
формой из двух дат), но возвращает интервалы, а не записи.

Для голого 409 это один запрос с `specialist_id__in=[...]` по составу тенанта —
дёшево. Для полноценного preview — N вызовов, по одному на мастера.

#### Инвариант никем не зафиксирован

Ни один тест не утверждает ни того, что четыре ручки проверку делают, ни того, что
не делают. Тесты исключений (`tenants/tests/test_schedule_admin_1062.py:268-328`) и
закрытий (`:331-395`) **не создают ни одной записи**. Для сравнения: 409 на
недоступности документирован как намеренный прямо в теле теста (`:402-403`).

То есть добавление guard'а не сломает ни одного теста — но и текущее молчание нигде
не зафиксировано как решение.

#### Ответ на прямой вопрос

**PARTIALLY.**

* **Закрывается почти без архитектуры:** №5 (исключение даты «не работаю») и №6
  (закрытие салона). Оба ограничены датой, и вопрос там ровно тот, на который
  существующий запрос умеет отвечать — «что внутри окна».
* **Требует отдельной реализации:** №2 и №3 (недельный шаблон) — развёртка по
  горизонту плюс несуществующий диф рамки; №4 (сокращение часов на дату) — два окна
  вместо одного плюс тот же диф.

**Предложение снимать не надо — надо сузить.** Жёсткий 409 даётся там, где
сокращение ограничено датой. Недельный шаблон в P0 либо остаётся без guard'а с
явной пометкой в UX, либо выводится из салонной P0-поверхности.

Первым ходом в любом случае — извлечь общий хелпер из трёх существующих копий.

## 3. Таблица Conflict Guard

Полностью в разделе В-4 выше — шесть операций, текущая проверка, 409/preview/token и минимальное изменение по каждой.

## 4. Reschedule: должен ли меняться master_id

Полностью в разделе В-3 выше. Короткий ответ: **не должен и не может**. Поля мастера нет ни в одном сериализаторе переноса и нет в `RescheduleBookingDTO`; мастер читается из самой записи (`cancel_reschedule_service.py:306`), а запись ограничена allowlist из четырёх полей (`:451-453`). Запрет в P0 — это фиксация существующего поведения, а не изменение.
## 5. Модель прав в P0 — что есть и что минимально нужно

**Есть:** `appointments/authz.py` — object- и action-aware резолвер актора, уже
применённый к `complete` и `no_show` (`appointments/views.py:434, 479, 566, 596`;
`tenants/appointments_api.py:709`). Полномочие в салоне даёт исключительно
`TenantUserRelationship` с `role=admin`; `User.role` при этом не читается
(`users/permissions.py:339-344`).

**Минимально нужно:** три функции уровня действия в том же модуле
(`may_cancel`, `may_reschedule`, `may_change_schedule`) и их вызов внутри
соответствующих действий — по образцу уже работающего `resolve_booking_operator`.
Пилотному администратору все три отвечают «да».

**Не нужно:** редактор ролей, RBAC, модель capability, новые таблицы. Владелец
явно разрешил выдать пилотному админу весь набор сразу — важно лишь, что различие
остаётся **в коде**, и место для него уже построено.

---

## 6. Read/write путь Ayla

### Write — менять нечего

`IsBotServiceWithVerifiedClient` (`users/permissions.py:134-207`) реализует ровно
целевую схему: сервисный Bearer доказывает звонок, `X-External-User-ID` называет
человека, `request.user` становится этим человеком (`:206`), `IsTenantAdmin`
проверяет его полномочие в этом салоне. Атрибуция идёт от `request.user.id`
(`tenants/appointments_api.py:509`, `:578`).

Команды, которые Ayla может исполнять этим путём **сегодня**: создание
(`tenants/appointments_api.py:338`), перенос (`:484`), отмена (`:553`), завершение
визита (`:624`), поиск клиента (`:251`).

Единственное условие — операционное, не кодовое: администратор должен быть заведён
как клиентский аккаунт и повышен связью, после чего его бот-идентичность один раз
связывается через `bind_external_identity`. Подробности и обоснование — в В-1.

### Read — три ручки готовы, четыре надо разделить

| Открывать read-credential | Класс | Почему |
|---|---|---|
| **можно как есть** | `TenantDayView` (`tenants/day_api.py:35`) | только `get` (`:66`) |
| **можно как есть** | `SalonCustomerLookupView` (`tenants/appointments_api.py:251`) | только `get` (`:286`) |
| **можно как есть** | `AdminScheduleImpactView` (`users/schedule_admin_api.py:183`) | только `get` (`:203`), и он read-only по смыслу (`:186-187`) |
| **нельзя** | `AdminScheduleView` (`users/schedule_admin_api.py:81`) | `get` + `put` + `patch` под одним списком (`:84-91`) |
| **нельзя** | `AdminScheduleExceptionListView` (`:381`) | `get` + `put` (`:401`, `:426`) |
| **нельзя** | `AdminTimeOffListView` (`:220`) | `get` + `post` (`:235`, `:238`) |
| **нельзя** | `TenantClosureListView` (`:520`) | `get` + `post` (`:541`, `:561`) |

Пометодной гранулярности в дереве нет: `get_permissions` и `has_object_permission`
не встречаются ни разу. Permissions в DRF задаются на класс, поэтому открыть
чтение у смешанного класса значит открыть и запись.

**Минимальный набор для P0:** отдельный read-only токен по образцу
`IsIdentityProvisioningBearer` (`users/permissions.py:254-305`, включая проверку
неравенства секретов и системный чек `users.E001`), подключённый к трём GET-only
классам; четыре смешанных класса — вынести GET в отдельные view с тем же
queryset и тем же сериализатором.

---

## 7. Обновлённая матрица 15 сценариев Ayla

Статус даётся для **целевой архитектуры владельца** (read — сервисный credential,
write — существующая поверхность с actor'ом человека), а не для сегодняшнего дня.
Где статус зависит от минимальной доработки, она названа.

| # | Сценарий | Статус | Доказательство из кода |
|---|---|---|---|
| 1 | Что происходит в салоне сегодня? | **SUPPORTED** | `GET /api/v1/tenants/me/day/` — `tenants/day_api.py:35-91`, сборка `tenant_day_service.py:143-353`. Класс GET-only (`:66`), открывается read-credential без расширения записи |
| 2 | Что изменилось сегодня? | **NOT SUPPORTED** | read-API событий нет ни одного: `grep` по всем `*urls.py` не даёт ни `outbox`, ни `events/`. Изменения графика событий не порождают (`users/schedule_api.py:257-338`, `users/schedule_admin_api.py:435-452`, `:568-586` — только `logger.info`). Атрибуция салонных действий сломана (P0-J первого прохода) |
| 3 | Кто работает сейчас? | **SUPPORTED** | та же проекция дня: `working_intervals`, `breaks`, `is_working_day`, `schedule_source` — `tenant_day_service.py:266-282`, `:328-340`. Мастера без записей возвращаются намеренно (`:24-26`) |
| 4 | Кто свободен после 16:00 для конкретной услуги? | **NOT SUPPORTED** | салонной ручки доступности нет (`tenants/urls.py:34-124` — ни `slots/`, ни `availability/`); многомастерного поиска нет вовсе — `specialist_id` скаляр в DTO (`appointments/application/dto.py:113`, `:121`) и в протоколе провайдера (`appointments/infrastructure/availability/providers.py:18-27`) |
| 5 | Найти запись клиента | **PARTIAL** | поиск клиента есть — `SalonCustomerLookupView`, `tenants/appointments_api.py:251-335`, GET-only (`:286`). Но отдаёт только `{id, name}` (`:327-330`), различить двух «Анн» нечем; записи ищутся только через проекцию дня по датам, поиска по клиенту нет |
| 6 | Создать запись | **PARTIAL** | команда есть и безопасна — `SalonBookingCreateView`, `tenants/appointments_api.py:338-481`: `CreateBookingService` с advisory-lock, обязательный `X-Idempotency-Key` (`:433-441`), actor человека (`:455`). Но выбрать время не из чего — см. №4 |
| 7 | Перенести запись у того же мастера | **SUPPORTED** | `SalonBookingRescheduleView`, `tenants/appointments_api.py:484-550`; атомарная доменная операция, обязательный `expected_version` (`:202`), 409 `STALE_VERSION` (`:524-525`). Оговорка: рабочие часы намеренно не проверяются (`cancel_reschedule_service.py:428-433`) |
| 8 | Сменить мастера существующей записи | **NOT SUPPORTED — и это правильно** | поля мастера нет ни в одном из четырёх сериализаторов переноса, нет в `RescheduleBookingDTO` (`appointments/application/dto.py:76-108`), мастер читается из самой записи (`cancel_reschedule_service.py:306`), запись ограничена allowlist'ом четырёх полей (`:451-453`). См. В-3 |
| 9 | Отменить запись | **SUPPORTED** | `SalonBookingCancelView`, `tenants/appointments_api.py:553-603`; закрытый allowlist причин (`:211-214`), корректный `initiator_role="salon"` (`:579`). Дефект атрибуции в событии (P0-J) на саму операцию не влияет |
| 10 | Сделать мастера завтра неработающим | **PARTIAL — небезопасно** | ручка есть: `PUT .../schedule-exceptions/` с `is_working_day=false` (`users/schedule_admin_api.py:426-452`). Но проверки активных записей нет — 200 поверх живой записи. См. таблицу Conflict Guard |
| 11 | Сделать мастера недоступным 13:00–15:00 | **SUPPORTED** | `POST .../time-off/` (`users/schedule_admin_api.py:238-295`) — единственная операция с полным Conflict Guard: preview + `impact_token` + 409 `IMPACT_CHANGED` + атомарное применение |
| 12 | Конфликт создания | **SUPPORTED** | `SlotNotAvailableError` → 409 `SLOT_NOT_AVAILABLE` (`tenants/appointments_api.py:460-463`); force-create отсутствует |
| 13 | Конфликт переноса | **SUPPORTED** | overlap-запрос под `select_for_update` с фильтром `ACTIVE_BOOKING_STATUSES` (`cancel_reschedule_service.py:435-440`) → 409; исходная запись не меняется |
| 14 | Конфликт сокращения графика | **PARTIAL** | полноценно только на недоступности. На недельном шаблоне, исключении даты и закрытии салона проверки нет вовсе — см. таблицу Conflict Guard |
| 15 | Unknown result / authoritative readback | **SUPPORTED** | каждая салонная команда возвращает authoritative состояние: создание — `AppointmentDetailSerializer` после `.get()` (`tenants/appointments_api.py:469-481`), перенос — `refresh_from_db()` + `revision_id` (`:543-545`), отмена — `refresh_from_db()` (`:596`). Повторное чтение записи для сверки — `GET /api/v1/internal/appointments/{id}/` (`appointments/internal_api.py:502`). Идемпотентность создания реальная: `CreateBookingService` ищет по `idempotency_key` и возвращает существующую запись (`tenants/appointments_api.py:411-432`) |

**Сводка: SUPPORTED 8, PARTIAL 4, NOT SUPPORTED 3.**

Три NOT SUPPORTED — это ровно те три, что были названы в первом проходе:
лента изменений, поиск свободного времени и смена мастера. Первые два надо строить,
третий строить не надо — он запрещён намеренно.
## 8. Подтверждённые P0-пробелы после второго прохода

Из первого прохода снимаются два: **P0-E** (различие прав — машинерия есть,
я ошибся) и **P0-C** в части «это пробел» (смена мастера запрещена намеренно, это
не пробел, а решение).

Остаются подтверждёнными:

1. **Слоя «Изменения» не существует.** Read-API событий нет ни одного во всём
   URL-дереве; `tenant_id` лежит только внутри JSONB `payload`, колонки и индекса
   нет. Маркера «последний просмотр сотрудником» нет. Объекта обращения нет.
2. **Атрибуция салонных действий сломана.** `cancel_reschedule_service.py:251, 477,
   492` — три самодельных тернарника без ветки `salon`, поэтому салонная отмена
   уходит как `actor: "user"`. Правильные таблицы рядом и не вызываются
   (`appointments/domain/value_objects.py:170-177`). Починка — три вызова.
3. **Изменения графика не порождают событий.** Четыре места записи
   (`schedule_api.py:257-338`, `schedule_admin_api.py:435-452`, `:568-586`,
   `internal_schedule_api.py:149`) пишут в базу и логируют строку.
4. **Салонной доступности нет.** В `tenants/urls.py:34-124` нет ни `slots/`, ни
   `availability/`. Многомастерного поиска нет структурно: `specialist_id` —
   скаляр в DTO (`appointments/application/dto.py:113`, `:121`) и в протоколе
   провайдера (`appointments/infrastructure/availability/providers.py:18-27`).
5. **Conflict Guard на сокращение доступности** — детально в В-4.
6. **Read-credential для Ayla не существует**, а единый `AYLA_INTERNAL_API_TOKEN`
   обслуживает и чтение, и запись (`users/permissions.py:181`, `:241`).
7. **Пилотный салон без администратора.** Замер 23.08: единственная учётка с
   `role=admin` принадлежит `ayla-marketplace`, у `formula-tela` их ноль.
   Подтверждено нулями в таблицах исключений, закрытий, недоступностей и ревизий —
   поверхность DRF-1062/1063 не использовалась ни разу.
8. **Инвариант неизменности мастера не закреплён тестом.** Ни один тест не читает
   `specialist_id` после переноса; `update_fields`-allowlist и состав DTO держат
   его фактически, но регрессия пройдёт молча.
9. **Кабинет** на карточке записи (DRF-1236/1237) не имеет источника — поля нет ни
   в модели, ни в проекции.
10. **Маскированного телефона** для разведения одноимённых клиентов backend не
    отдаёт; поиск возвращает только `{id, name}` (`tenants/appointments_api.py:327-330`).

---

## 9. Frozen UX, требующий поправки

Ни одна задача в Linear не менялась. Ниже — точные формулировки для владельца.

### DRF-1237 — общее расписание салона

| Что зафиксировано | Что должно измениться | Тип |
|---|---|---|
| «Отдельное действие `Найти время для записи` открывает service-aware поиск availability» | Backend отсутствует полностью. Либо действие выводится из P0, либо задача явно зависит от новой ручки | **backend gap** |
| «сначала отбирает мастеров, которые могут выполнять услугу» | Многомастерного поиска нет структурно — движок одномастерный | **backend gap** |
| «свободный интервал — это диапазон доступности, а не готовый Appointment slot» | Read-путь отдаёт фиксированную сетку 30 минут (`slot_builder.py:20`), представления диапазона нет | **contract gap** |
| Смена мастера подразумевается в поиске времени | Смена мастера из P0 уходит (В-3) | **текстовая аннотация** |

### DRF-1239 — детали записи, перенос, отмена

| Что зафиксировано | Что должно измениться | Тип |
|---|---|---|
| «новые **мастер**/время берутся только из authoritative availability» | Убрать мастера. Перенос = то же время, тот же мастер (В-3) | **текстовая аннотация** |
| Предложение переноса вида «Денис → Инна» | Убрать из макета. Другой мастер = новая запись + отдельная отмена, как уже сделано при увольнении мастера (`users/services.py:1274-1280`) | **правка макета** |
| «только из authoritative availability» | Неточно и во второй половине: салонный перенос **намеренно не проверяет** рабочие часы и закрытия (`cancel_reschedule_service.py:428-433`, обоснование `:424-427`). Салон вправе перенести за пределы смены. Недоступность при этом не обходится никогда | **текстовая аннотация** |

### DRF-1240 — рабочее время и недоступность

| Что зафиксировано | Что должно измениться | Тип |
|---|---|---|
| Пункт 3 финального freeze: authoritative impact preview обязателен на пяти операциях | Полный preview остаётся только на недоступности. Исключение даты «не работаю» и закрытие салона получают жёсткий 409. Сокращение часов на дату и недельный шаблон — либо отдельная работа, либо вне P0 | **contract gap + правка макета** |
| Conflict Guard как общее правило | Правило сохраняется, меняется **форма**: где-то preview с разрешениями, где-то отказ с переходом к записи | **текстовая аннотация** |

### DRF-1241 — системные состояния и права

| Что зафиксировано | Что должно измениться | Тип |
|---|---|---|
| «object permission и action permission разделены» | **Ничего.** Это подтверждается кодом — `appointments/authz.py`. Моя правка первого прохода была неверна | — |
| «запрещённое действие не показывается как активная кнопка» | Ни одна ручка не возвращает список разрешённых действий. Либо UI выводит их из роли локально, либо нужен `allowed_actions` в ответе | **contract gap** |

### DRF-1249 — Ayla для салона

Что Ayla реально сможет предлагать как write после этих решений:
**создать запись, перенести у того же мастера, отменить, завершить визит,
поставить недоступность с разрешением конфликтов.**

Чего не сможет: сменить мастера (запрещено намеренно), менять недельный график
(без guard'а), показывать ленту изменений и «Требует внимания» (нет источника).

### DRF-1236 — «Сегодня»

Блок `Ayla · Изменения` и счётчик «N новых» источника не имеют. «Кабинет» на
карточке записи не имеет поля. **backend gap** в обоих случаях.

### DRF-1062 — эпик

Описание утверждает про логику графика «запрет закрывать время при активных
записях (409)». По коду запрет есть только в `TimeOffListView.post`; в
`ScheduleView.put/patch` его нет. **Формулировку поправить** — она переносит
свойство одной ручки на весь модуль.

---

## 10. Решения владельца

После чтения кода технически невыводимыми остались три. Остальное решено.

### Р-1. Слой «Изменения» — строить или вырезать из P0?

Это единственный крупный объём, оставшийся в DRF-1249: read-API событий, колонка и
индекс `tenant_id` на `OutboxEvent`, маркер последнего просмотра, модель обращения,
события на изменения графика, плюс починка атрибуции.

Технически выводится всё, кроме одного: **входит ли это в Controlled Pilot.**
Без решения нельзя ни рисовать вкладку Ayla, ни замораживать DRF-1236 целиком.

### Р-2. Недельный график в P0 — оставить без guard'а или убрать с поверхности?

Дёшево закрыть нельзя (В-4). Три варианта: оставить как есть с явной пометкой в
UX, что операция не защищена; убрать редактирование недельного шаблона из салонной
P0-поверхности, оставив исключения дат; или принять отдельную работу.

Риск-калибровка тут владельческая: на пилоте одна будущая запись, и цена ошибки
сегодня близка к нулю — но поверхность останется.

### Р-3. Поиск времени — строить или обойтись журналом дня?

Технически есть **третий путь, который в первом проходе я не назвал**: время можно
выбрать глазами по журналу дня, а безопасность обеспечит сам `create` — он вернёт
409 `SLOT_NOT_AVAILABLE`, если время занято (`tenants/appointments_api.py:460-463`),
и force-create отсутствует. Ошибиться необратимо нельзя.

Это делает создание записи рабочим в P0 **без** новой ручки доступности — ценой
того, что Ayla не сможет сама предложить варианты, а сможет только проверить
предложенное. Вопрос владельцу: достаточно ли этого для пилота.


---

# ПЕРВЫЙ ПРОХОД (23.08) — сохранён для истории

> Ниже исходный отчёт первого прохода. Два его вывода второй проход исправил:
> **P0-E** (различие прав — машинерия есть, см. В-2) и трактовка **P0-C**
> (смена мастера — не пробел, а намеренное решение, см. В-3).

# DRF-1297 — Агент-аудит: Ayla для салона. Сверка UX, Linear и backend

> Аудит read-only. В коде, в Linear и в базах пилота ничего не менялось.
>
> **База сверки по коду:** `AndreyDeveloper84/beautygo_backend`, ветка `dev`,
> коммит `e07388ffe21e3a5c339229f70b84cccf234d2e49` (2026-08-22 21:06 +03).
> Все ссылки `файл:строка` относятся к этому дереву.
>
> **База сверки по пилоту:** контейнеры `dev-web-1` / `dev-db-1` на 194.87.99.126,
> 23.08. Проверено, что выложенный на пилоте `tenants/appointments_api.py` совпадает
> с `dev` по объёму и содержит правки DRF-1231, то есть находки применимы к живому
> контуру, а не только к ветке.
>
> **Читано в Linear:** DRF-1236, 1237, 1238, 1239, 1240, 1241, 1249, 1062, 1063 —
> описания и все финальные freeze-комментарии.

---

## 1. Вердикт

# NOT READY

Не потому, что «ничего не работает» — работает многое, и хорошо. А потому, что
**backend отсутствует ровно под тем, чем DRF-1249 является по определению владельца.**

Owner decision в DRF-1249 задаёт Salon Ayla как три слоя: **Состояние → Изменения →
Требует внимания.**

| Слой | Состояние backend |
|---|---|
| **1. Состояние** — что происходит сейчас | **есть и достаточно** — `GET /api/v1/tenants/me/day/`, `tenants/day_api.py:35` |
| **2. Изменения** — что произошло | **0 %** — нет ни одного read-эндпоинта событий во всём URL-дереве; изменения графика не порождают событий вовсе; счётчика «N новых с последнего просмотра» не на чем построить |
| **3. Требует внимания** — незакрытые ситуации | **0 %** — ни модели, ни поля, ни производного статуса; объекта «обращение клиента» не существует |

К этому добавляются три вещи, каждой из которых по отдельности хватило бы на «не
замораживать»:

1. **Подтверждённый дефект атрибуции.** Салонная отмена уходит в событие как
   `actor: "user"` — «отменил клиент». Ayla, построенная поверх этих событий, будет
   уверенно врать администратору про его же действие. Это прямо запрещено owner
   decision DRF-1249 («Ayla не приписывает себе чужие действия и не превращает
   предположение в факт»). Подробно — **P0-J**.
2. **Ключевой разговорный сценарий не замыкается.** «Подбери время и запиши» требует
   доступности. Салонной поверхности доступности **не существует ни одной ручки**,
   а многомастерного поиска времени — вообще ни в каком виде. Подробно — **P0-K**.
3. **Читающая и пишущая половины салона требуют разных способов аутентификации,
   и Ayla попадает только в одну из них.** Бот может создать, перенести, отменить и
   закрыть запись — и не может прочитать журнал дня и тронуть график. Подробно — **P0-D**.

Что это **не** значит: ни одна из находок не является архитектурным тупиком. Схема
данных под большинство пробелов уже есть, и почти всё — аддитивная работа. Речь о том,
что замораживать макет поверх несуществующего контракта преждевременно.

Что делать вместо заморозки целиком — раздел 4 («Что уже можно рисовать») и раздел 6
(четыре решения владельца, без которых работа встанет).

---

## 2. Матрица сценариев

В брифе аудита перечислено **16** пунктов, а не 15 (10 на чтение, 6 на действия).
Ниже все шестнадцать.

Легенда: **ЕСТЬ** — реализовано и достижимо салоном · **ЧАСТИЧНО** — есть основа, но
не хватает существенного · **НЕТ** — не существует.

### Чтение

| # | Сценарий | Есть? | Где именно | Чего не хватает |
|---|---|---|---|---|
| 1 | tenant-day / сегодняшний день салона | **ЕСТЬ** | `tenants/day_api.py:35-91`, сборка `appointments/application/services/tenant_day_service.py:143-353`; маршрут `tenants/urls.py:42-46` | «Сегодня» считается по таймзоне первого попавшегося мастера (`day_api.py:108-117`). Фильтра по статусу нет (`tenant_day_service.py:234-245`) — отменённые записи приходят вместе с живыми, разбирать их обязан клиент по `by_status`. Свободных интервалов проекция не считает |
| 2 | записи салона и конкретная запись | **ЧАСТИЧНО** | список — та же проекция дня, `DayBooking` c `version` и `status` (`tenant_day_service.py:60-76`); карточка одной записи — `GET /api/v1/internal/appointments/{id}/`, `appointments/internal_api.py:502` | Карточка отдаёт **четыре поля** и существует только чтобы выдать `expected_version`. Полного Appointment Detail с историей, инициатором и доступными действиями нет. Поля «кабинет» нет нигде (**P0-I**) |
| 3 | мастера и их рабочие часы | **ЕСТЬ** | `GET /api/v1/tenants/me/masters/{id}/schedule/` — `AdminScheduleView`, `users/schedule_admin_api.py:81-91`; в проекции дня — `working_intervals` + `breaks` + `schedule_source` (`tenant_day_service.py:266-282`) | Читается только по JWT + `X-App-Type: pro`; боту недоступно (**P0-D**) |
| 4 | исключения конкретной даты | **ЕСТЬ** | `GET .../schedule-exceptions/?date_from=&date_to=` — `users/schedule_admin_api.py:401-414`; модель `SpecialistScheduleException`, `appointments/models.py:574-619` | То же ограничение по аутентификации. На пилоте таблица пуста — 0 строк, поверхность ни разу не использовалась |
| 5 | частичная недоступность | **ЕСТЬ** | `GET .../time-off/` — `AdminTimeOffListView`, `users/schedule_admin_api.py:235-236`; в проекции дня — `DayAbsence` (`tenant_day_service.py:49-57`, `:284-294`) | На пилоте 0 строк |
| 6 | доступность и свободное время | **НЕТ** | движок есть и хороший — `AvailabilityQueryService`, `appointments/application/services/availability_query_service.py:39`; но наружу выходит **только** через `GET /api/v1/specialists/{id}/slots/` (`users/specialists_api.py:392`) и `GET /api/v1/internal/specialists/{id}/slots/` (`users/internal_catalog_api.py:45`) | **Салонной ручки доступности нет ни одной** — в `tenants/urls.py:34-124` нет ни `slots/`, ни `availability/`. Публичная ручка резолвит только маркетплейсный `Service` (`users/specialists_api.py:263-265`), которых у пилота ноль; салонный каталог понимает только внутренняя, под общим Bearer без скоупа тенанта. Многомастерного поиска нет вовсе (**P0-K**) |
| 7 | поиск и разрешение клиента | **ЕСТЬ** | `SalonCustomerLookupView`, `tenants/appointments_api.py:251-335`; маршрут `tenants/urls.py:51-55` | Отдаёт только `{id, name}`; `name` бывает пустым (`tenant_day_service.py:124-140`). Телефон намеренно не отдаётся (DRF-1039). Жёсткий срез 20 без `has_more` (`:277`, `:321`) — двух «Анн» в диалоге развести нечем (**P0-L**) |
| 8 | услуги и связь услуги с мастерами | **ЧАСТИЧНО** | модель полная и проиндексированная под нужный запрос: `SpecialistService`, `services/models.py:402-465`, индекс `specsvc_salon_active_idx` (`:459-462`); ручка `GET /api/v1/internal/catalog/specialist-services/?salon_service=`, `services/internal_api.py:58-75` | Ручка под `IsInternalBearer` (`:65`) — салонного JWT-пути нет, `/api/v1/tenants/me/services/` не существует. `tenant` там **фильтр, а не скоуп**: без параметра отдаётся каталог всех тенантов |
| 9 | operational events / журнал изменений | **НЕТ** | `OutboxEvent` есть и append-only (`appointments/models.py:796-953`), конверт несёт `tenant_id`, `actor`, `occurred_at` (`appointments/infrastructure/outbox/envelope.py:11-22`, `:183`) | **Read-API нет ни одного** — единственная поверхность чтения это Django-admin (`appointments/admin.py:129-145`), не скоупленный по тенанту. `tenant_id` только внутри JSONB, колонки и индекса нет. События графика не эмитятся вовсе (**P0-H**). Атрибуция салонных действий сломана (**P0-J**) |
| 10 | permission-aware представление данных | **ЧАСТИЧНО** | `IsTenantAdmin` корректно fail-closed при отсутствии тенанта (`users/permissions.py:335-344`); 404 вместо 403 на чужие объекты выдержано (`tenants/appointments_api.py:227-248`) | Различить «может читать» и «может действовать» **невозможно**: `has_object_permission` и `get_permissions` в дереве не встречаются ни разу; `Role.STAFF` существует в модели (`users/models.py:700-703`), но не читается ни одним permission-классом (**P0-E**) |

### Действия

| # | Сценарий | Есть? | Где именно | Чего не хватает |
|---|---|---|---|---|
| 11 | создание записи сотрудником | **ЕСТЬ** | `SalonBookingCreateView`, `tenants/appointments_api.py:338-481`; переиспользует `CreateBookingService` с advisory-lock и повторной проверкой, второго booking-движка нет; `X-Idempotency-Key` обязателен (`:433-441`) | Актор пишется только в событие (`create_booking_service.py:445`, `:469`), на строке `Appointment` полей `created_by`/`source`/`origin` нет — восстановить «кто записал» из состояния БД нельзя. Достижимо только бот-Bearer'ом (**P0-D**). Выбрать время не из чего (**P0-K**) |
| 12 | перенос | **ЧАСТИЧНО** | `SalonBookingRescheduleView`, `tenants/appointments_api.py:484-550`; одна атомарная доменная операция, `expected_version` обязателен, ревизия пишется корректно | **Сменить мастера нельзя** — в сериализаторе только `new_start_datetime` (`:191-202`), в DTO мастера нет (`:507-516`). UX DRF-1239/1237 смену мастера допускает (**P0-C**). Событие переноса врёт про инициатора (**P0-J**) |
| 13 | отмена | **ЧАСТИЧНО** | `SalonBookingCancelView`, `tenants/appointments_api.py:553-603`; закрытый allowlist `reason_code`, корректный `initiator_role="salon"` | Событие отмены врёт про инициатора: `actor: "user"` (**P0-J**) |
| 14 | изменение рабочего времени | **ЧАСТИЧНО** | недельный шаблон — `AdminScheduleView` (`users/schedule_admin_api.py:81-91`), исключение даты — `AdminScheduleExceptionListView.put` (`:426-452`), закрытие салона — `TenantClosureListView.post` (`:561-586`) | **Ни одна из трёх операций не проверяет активные записи** (**P0-A**). Ни одна не порождает события (**P0-H**). Боту недоступно (**P0-D**) |
| 15 | постановка недоступности | **ЕСТЬ** | `AdminTimeOffListView.post`, `users/schedule_admin_api.py:238-295`; 409 `HAS_ACTIVE_APPOINTMENTS` при живых записях (`users/schedule_api.py:428-431`) | Событий нет (**P0-H**); боту недоступно (**P0-D**) |
| 16 | разрешение конфликта поверх активных записей | **ЧАСТИЧНО** | лучший кусок всей салонной поверхности: `AdminScheduleImpactView` с `impact_token` (`users/schedule_admin_api.py:183-217`), атомарное применение через `apply_absence_with_resolutions` (`:243-295`), 409 `IMPACT_CHANGED` при устаревшем токене (`:269-277`), force-save отсутствует | Работает **только для недоступности** — под остальные четыре операции сокращения доступности Conflict Guard не подведён (**P0-A**). Единственное допустимое решение — `cancel`; перенести затронутую запись нельзя (**P0-B**) |

---

## 3. Подтверждённые P0-пробелы

Все пункты ниже проверены по коду или прямым замером пилота. Предположений нет.

### P0-A. Conflict Guard существует ровно для одной операции из пяти, которые его требуют

DRF-1240, финальный freeze 23.08, пункт 3, дословно:

> **Conflict Guard является общим правилом для любого сокращения доступности, а не только для частичной `Недоступности`.** Authoritative impact preview обязателен также когда: конкретный день переводят в `Не работаю`; часы конкретной даты сокращают; повторяющийся недельный день сокращают; рабочий день целиком закрывают; добавляют или расширяют частичную недоступность.

Реализована **только последняя** из пяти.

| Операция | Маршрут | Impact preview | Проверка активных записей |
|---|---|---|---|
| Частичная недоступность | `POST .../masters/{id}/time-off/` | **есть**, с `impact_token` — `users/schedule_admin_api.py:183-217` | **есть**, 409 — `users/schedule_api.py:428-431` |
| Недельный шаблон, PUT | `PUT .../masters/{id}/schedule/` | нет | **нет** — `ScheduleView.put` делает `delete()` + `bulk_create()` без единой проверки, `users/schedule_api.py:257-293` |
| Недельный шаблон, PATCH | `PATCH .../schedule/` | нет | **нет** — `users/schedule_api.py:306-338` |
| Исключение на дату («Не работаю», «Другие часы») | `PUT .../schedule-exceptions/` | нет | **нет** — `update_or_create` и сразу 200, `users/schedule_admin_api.py:435-452` |
| Закрытие всего салона | `POST /api/v1/tenants/me/closures/` | нет | **нет** — `users/schedule_admin_api.py:561-586`; единственный 409 здесь означает «закрытие на эту дату уже есть», а не «в этот день есть записи» |

На пилоте: администратор выставит мастеру «Не работаю» на дату, получит 200, слот-кэш
инвалидируется — а подтверждённая запись клиента останется живой в базе и продолжит
висеть в журнале дня. Молчаливой отмены не происходит (это хорошо), но и
предупреждения тоже (это и есть нарушение Conflict Guard).

**Расхождение Linear ↔ код.** Описание DRF-1062 утверждает про существующую логику
графика: «валидация, PUT всех семи дней, PATCH по дням, инвалидация кэша, **запрет
закрывать время при активных записях (409)**». По коду запрет относится исключительно
к `TimeOffListView.post`. В `ScheduleView.put/patch` его нет. Формулировка переносит
свойство одной ручки на весь модуль и в таком виде неверна.

### P0-B. Разрешение конфликта умеет только отменять

`users/schedule_admin_api.py:94-96`:

```python
class ResolutionSerializer(serializers.Serializer):
    appointment_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=["cancel"])
```

Единственное решение по вытесненной записи — `cancel`. Перенести затронутую запись в
рамках того же атомарного применения нельзя. UX DRF-1240 этого прямо и не требует
(там «переход к конфликтующей записи»), но Ayla не сможет предложить «перенесу Ольгу
на 16:00 и тогда закрою тебе вечер» одним подтверждённым действием — только «отменить»
либо ручной двухходовкой.

### P0-C. Перенос не умеет менять мастера

`SalonRescheduleSerializer` (`tenants/appointments_api.py:191-202`) принимает ровно два
поля: `new_start_datetime` и `expected_version`. `RescheduleBookingDTO`, собираемый в
`tenants/appointments_api.py:507-516`, мастера не несёт. В самом сервисе это
зафиксировано комментарием: «Wave 1 only supports same-ID, time-only reschedule (no
specialist/service change — that's the "Replacement" path… out of scope here)»
(`appointments/application/services/cancel_reschedule_service.py:497-500`).

DRF-1239 freeze: «новые **мастер**/время берутся только из authoritative availability».
DRF-1237 freeze: «сначала отбирает мастеров, которые могут выполнять услугу».
UX-канон смену исполнителя допускает — backend её не поддерживает.

### P0-D. Читающая и пишущая салонные поверхности требуют разных способов аутентификации

Самый неочевидный разрыв, и он бьёт ровно в DRF-1249.

**Чтение дня и весь график** — JWT живого пользователя + `X-App-Type: pro`:

* `TenantDayView`, `tenants/day_api.py:47-51` → `[IsAuthenticated, IsProApp, IsTenantAdmin]`
* `_ADMIN_PERMISSIONS`, `users/schedule_admin_api.py:44-48` → то же, применено в
  `:54, :194, :388, :459, :529, :592`

**Запись записей** — сервисный Bearer бота, JWT-аутентификатор отключён:

```python
# tenants/appointments_api.py:217-222
class _SalonBookingBase(APIView):
    authentication_classes: list = []
    permission_classes = _SALON_WRITE_PERMISSIONS   # IsBotServiceWithVerifiedClient, IsTenantAdmin
```

Список аутентификаторов опустошён намеренно (DRF-1231, обоснование в комментарии
`tenants/appointments_api.py:95-122`): `DEFAULT_AUTHENTICATION_CLASSES` содержит только
`JWTAuthentication` (`djangoProject/settings/base.py:72-74`), и сервисный Bearer он
отклонял 401 раньше, чем отрабатывали permissions.

Граница проведена ровно по двум префиксам: `AppTypeMiddleware.EXCLUDED_PATH_PREFIXES`
(`users/middleware.py:100-106`) выводит из-под `X-App-Type` только
`/api/v1/tenants/me/appointments/` и `/api/v1/tenants/me/customers/`. Остальные
двенадцать маршрутов `/tenants/me/` остаются под `IsProApp`, а исключение префикса,
как отмечено там же (`users/middleware.py:98-101`), делает `IsProApp` **навсегда
неудовлетворимым**, а не «мягче».

Следствие буквальное:

* **Ayla (бот) не может прочитать журнал дня** и не может тронуть график, недоступность
  и закрытия — её Bearer упрётся в JWT-аутентификатор;
* **мобильная салонная админка с обычным JWT администратора не может создать,
  перенести, отменить или закрыть запись** — там нет ни бот-Bearer, ни
  `X-External-User-ID`.

Ни одна из двух половин не покрывает Ayla целиком. У Ayla сегодня есть право писать
записи и нет права читать день.

### P0-E. Права: разделить «читать» и «действовать» сегодня невозможно

DRF-1239 и DRF-1241 фиксируют это как обязательное: «read permission и action
permission различаются», «object permission и action permission разделены»,
«запрещённое действие не показывается как активная кнопка».

Backend такого различия выразить не может:

* роли в `TenantUserRelationship.Role` ровно три — `customer`, `staff`, `admin`
  (`users/models.py:700-703`);
* **`Role.STAFF` не читает ни один permission-класс.** Вне тестов он встречается
  только в `users/services.py:51`, `users/services.py:686`, `users/signals.py:21` —
  то есть в резолве основного тенанта и каскаде увольнения, но не в авторизации;
* салонная авторизация целиком сводится к `role == ADMIN` в `request.tenant`
  (`IsTenantAdmin`, `users/permissions.py:307-344`);
* гранулярности нет: `has_object_permission` и `get_permissions` не встречаются в
  дереве **ни разу**. Все салонные маршруты закрыты одним списком на уровне класса.

Практически: **любой, кто может открыть журнал дня, может и отменить запись.**
Это не «не дорисовали экран» — это отсутствующая машинерия.

### P0-F. Ayla не знает ни одного салонного действия

В этом репозитории Ayla как разговорный агент существует, но только клиентская.
`ai/tools.py:152-158`:

```python
TOOL_DEFINITIONS = [SHOW_SPECIALISTS, SHOW_SLOTS, CONFIRM_BOOKING,
                    SHOW_APPOINTMENTS, ASK_CLARIFICATION]
```

Пять инструментов, все клиентские; `show_appointments` ограничен записями самого
клиента (`ai/tools.py:109-111`), `confirm_booking` ничего не создаёт
(`ai/tools.py:83-85`). Инструментов к журналу дня, салонным операциям и графику нет.
Реестра «навыков» поверх этого списка тоже нет. Салонный разговорный слой живёт в
другом репозитории (бот / `ai-bot-platform`) — этот аудит проверял, есть ли там за что
зацепиться со стороны Ayla.

### P0-G. У пилотного салона нет ни одного администратора — поверхность физически недоступна

Замер по боевой базе пилота 23.08 (`dev-db-1`, БД `beautygo`, только SELECT):

```
role       | count            tenants_tenant
client     |    22            f6f4efe1…  ayla-marketplace
specialist |     9            b32a057a…  formula-tela
admin      |     1            + 4 mkt-*
```

Единственная учётка с ролью `admin` — `bf333aca-af1f-47b8-9f6a-a228b86ecf2d`,
`username='admin'`, `tenant_id = f6f4efe1…`, то есть **`ayla-marketplace`**, а не
пилотный салон. У `formula-tela` администраторов ноль.

`IsTenantAdmin` требует активную связь `role=ADMIN` именно в адресованном тенанте
(`users/permissions.py:338-344`), поэтому **все салонные маршруты для пилотного салона
сегодня отдают 403 просто потому, что действовать некому.**

Команда существует — `users/management/commands/provision_salon_admin.py`, — и её
собственный docstring (`:3-8`) фиксирует ровно это состояние с аудита 14.08. За девять
дней она к `formula-tela` не применялась. Косвенное подтверждение: на пилоте 0 строк в
`appointments_specialistscheduleexception`, `appointments_tenantclosure`,
`appointments_specialisttimeoff` и `appointments_appointmentrevision` — вся
поверхность DRF-1062/1063 ни разу не использовалась живым человеком.

**Ловушка в самой команде:** в финальной инструкции (`:132-136`) она печатает
`X-Tenant: {tenant.id}`, тогда как `TenantContextMiddleware` резолвит заголовок
**по slug** (`users/middleware.py:278`). Кто выполнит инструкцию буквально — получит
`request.tenant = None` и 403 без объяснения.

### P0-H. Изменения графика не порождают ни одного события

Замер по пилоту, все темы outbox за всё время:

```
booking.created        27
booking.confirmed      20
booking.cancelled      17
booking.rescheduled     2
appointment.rescheduled 2
```

Событий о графике, недоступности, исключениях даты и закрытиях салона **не существует
ни одного типа**, и в `OutboxEvent.Topic` (`appointments/models.py:817-857`) их тоже
нет. По коду согласуется: `ScheduleView.put/patch` (`users/schedule_api.py:257-338`),
`AdminScheduleExceptionListView.put` (`users/schedule_admin_api.py:435-452`),
`TenantClosureListView.post` (`:568-586`) и `users/internal_schedule_api.py:149` пишут
в базу и логируют строку в `logger.info` — и всё. Единственная реакция на запись
графика — инвалидация слот-кэша через сигналы (`appointments/signals.py:33-57`).

DRF-1249 owner decision требует, чтобы слой «Изменения» показывал «новая запись,
отмена, перенос, **изменение доступности** и другие подтверждённые события». Изменение
доступности в этот список попасть не может — его неоткуда взять.

Заодно подтверждён замер главного окна: темы `booking.completed` в базе пилота нет ни
одной. Ни один визит действительно никогда не закрывался. Причина в коде ровно та,
что названа в DRF-1048: `appointments/tasks.py:284-285`

```python
if not getattr(settings, "BOOKING_AUTO_COMPLETE_ENABLED", False):
    return None
```

— возврат без единой строки в лог. На пилоте переменной в окружении `dev-web-1` нет
вовсе (проверено `printenv`). *Мелкое уточнение к замеру главного окна: функция
возвращает `None`, а не `{"ran": False}`; на существо это не влияет.*

### P0-I. «Кабинет» на карточке записи не имеет источника

DRF-1236 freeze: «на карточке записи показываются клиент, услуга, длительность, мастер
и при наличии **кабинет** как операционный контекст». DRF-1237 freeze: «мастер и
кабинет показываются в карточке как операционный контекст».

Поиск по `appointments/`, `tenants/`, `users/`, `services/` по `cabinet`, `room`,
`кабинет` даёт **один** результат — и тот в комментарии про личный кабинет мастера
(`appointments/views.py:220`). У модели `Appointment` поля кабинета нет; в `DayBooking`
(`appointments/application/services/tenant_day_service.py:60-76`) — тоже.

Freeze-комментарии аккуратно оговаривают, что кабинет **не ресурс** без реальной
backend-проверки. Но они предполагают, что его хотя бы можно **показать**. Показывать
нечего.

### P0-J. Событие салонной отмены говорит, что отменил клиент

Самая дорогая находка аудита: она бьёт ровно в то, ради чего DRF-1249 существует —
«Каждое изменение должно иметь достоверный источник/инициатора».

Канонические таблицы соответствия написаны правильно и **включают салон**
(`appointments/domain/value_objects.py:170-177`):

```python
_ENVELOPE_ACTOR = {
    OperationalActor.CLIENT.value: "user",
    "user": "user",
    OperationalActor.SPECIALIST.value: "admin",
    OperationalActor.SALON.value: "admin",      # <-- есть
    OperationalActor.SYSTEM.value: "system",
}
```

Но в сервисе отмены и переноса они **не вызываются**. Вместо них стоят три самодельных
тернарника, написанных ещё когда словарь актора был трёхзначным — что прямо видно по
комментарию над первым (`cancel_reschedule_service.py:245`: «`{client, specialist,
system}`. Map to ADR-0009 actor»):

```python
# cancel_reschedule_service.py:251-255  — envelope actor при отмене
actor=("admin" if initiator_role == "specialist"
       else "system" if initiator_role == "system"
       else "user"),

# cancel_reschedule_service.py:477-481  — payload rescheduled_by при переносе
rescheduled_by = ("master" if initiator_role == "specialist"
                  else "system" if initiator_role == "system"
                  else "user")

# cancel_reschedule_service.py:492-496  — payload registry_actor при переносе
registry_actor = ("specialist" if initiator_role == "specialist"
                  else "system" if initiator_role == "system"
                  else "user")
```

Ветки для `salon` нет ни в одном. Салонные ручки передают именно её —
`OperationalActor.SALON.value` в `tenants/appointments_api.py:511` (перенос) и
`tenants/appointments_api.py:579` (отмена). Значит `salon` проваливается в `else` и
становится `"user"`.

**На пилоте это значит:** администратор отменяет запись на ресепшене — в событии
`booking.cancelled` в конверте стоит `actor: "user"`, «отменил клиент». Переносит —
в `appointment.rescheduled` стоит `rescheduled_by: "user"` и `registry_actor: "user"`.
Ayla поверх этих событий будет уверенно и неправильно рассказывать администратору,
что клиент сам отменил запись, которую только что отменил он сам.

**Не всё сломано.** Поле `cancelled_by` в payload считается через правильный
`cancelled_by_for` (`cancel_reschedule_service.py:105`) и даёт `"admin"`;
`AppointmentRevision.actor_role` пишется корректно (`:463`); `booking.completed`
использует `envelope_actor_for` (`completion.py:65`) и не затронут. То есть починка —
это три вызова уже существующих функций, а не новая модель.

Почему не отловилось: `envelope_actor_for` намеренно не бросает исключение на
неизвестном значении, а молча отдаёт `"user"` (`value_objects.py:210-217`). Тихий
фолбэк и делает ошибку невидимой в логах.

### P0-K. Доступности для салона не существует, а многомастерного поиска — тем более

Движок хороший и единый для чтения и записи: `AvailabilityQueryService`
(`appointments/application/services/availability_query_service.py:39`), и путь записи
ходит в тот же `_get_working_hours` (`appointments/application/services/_booking_guards.py:139`),
так что чтение и запись разойтись не могут. Цепочка приоритетов ратифицированная и
корректная — **проверено**: исключение даты возвращается раньше недельного шаблона
(ранний `return` на `availability_query_service.py:225`, шаблон при этом не
запрашивается вовсе), а busy-интервалы (записи, TimeOff, закрытия тенанта)
вычитаются безусловно, независимо от того, какая рамка победила
(`:170-193`). Инвариант владельца «TimeOff сильнее открывающего исключения» в коде
держится.

Наружу это выходит через **две** ручки, и салону не подходит ни одна:

| Маршрут | Права | Почему не подходит |
|---|---|---|
| `GET /api/v1/specialists/{id}/slots/` (`users/specialists_api.py:392`) | `[IsAuthenticated]` (`:367`) | Резолвит только маркетплейсный `Service` (`:263-265`), которых у пилота ноль; `allow_salon_fallback` по умолчанию `False` (`:197`). Скоупа по тенанту в queryset нет вообще (`:418-470`) |
| `GET /api/v1/internal/specialists/{id}/slots/` (`users/internal_catalog_api.py:45`) | `[IsInternalBearer]` (`:43`) | Общий сервисный токен, `request.user` остаётся анонимным (`users/permissions.py:216-218`), скоупа тенанта нет никакого |

В `tenants/urls.py:34-124` — все четырнадцать салонных маршрутов — **нет ни `slots/`,
ни `availability/`, ни `free-time/`.**

**Многомастерного поиска («кто из моих мастеров может сделать эту услугу вечером»)
не существует ни в каком виде.** Движок структурно одномастерный: `specialist_id` —
скаляр в обоих DTO (`appointments/application/dto.py:113`, `:121`), и сам протокол
провайдера занятости берёт одного специалиста (`appointments/infrastructure/availability/providers.py:18-27`).
`get_week_availability` разворачивается по датам, никогда по мастерам. Единственный
fan-out по мастерам во всём дереве — `build_tenant_day`
(`tenant_day_service.py:170-175`), и он слот-билдер не вызывает вовсе.

Это прямо ломает DRF-1237 freeze («Поиск времени для записи… сначала отбирает мастеров,
которые могут выполнять услугу») и DRF-1238 freeze («выбор услуги определяет список
eligible masters»).

Дополнительно, что важно для формулировок Ayla: ответ приходит **фиксированной сеткой,
а не свободными интервалами** — шаг 30 минут (`appointments/infrastructure/availability/slot_builder.py:20`,
`djangoProject/settings/base.py:380`), минимальный запас 60 минут
(`slot_builder.py:23`), слоты раньше `now + 60min` молча отбрасываются. На вопрос
«есть что-нибудь через полчаса?» ответ всегда будет «нет» — by design. Представления
свободного диапазона в read-пути нет ни одного: `TimeInterval` используется
исключительно для занятого времени.

`buffer_after_minutes` учитывается (`slot_builder.py:52-54`), но только на внутренней
ручке с `allow_salon_fallback=True`, и в ответе он невидим — `end_at` считается по
чистой длительности (`:55`, `:79`).

### P0-L. Разрешение клиента в диалоге упирается в два поля

`SalonCustomerLookupView` отдаёт строго `{"id", "name"}` (`tenants/appointments_api.py:327-330`).
Телефон намеренно отсутствует (owner decision DRF-1039), маскированного варианта нет.
`name` бывает пустой строкой — намеренно (`tenant_day_service.py:124-140`).
Дополнительных различителей — последний визит, число записей — нет.

DRF-1238 freeze требует состояния «неоднозначный клиент с безопасным различением» и
говорит «телефон в disambiguation показывается маскированным». **Маскированного
телефона backend не отдаёт.** Два клиента по имени «Анна» приходят как два одинаковых
объекта, различимых только UUID.

Плюс жёсткий срез: `LIMIT = 20` (`:277`) применяется срезом (`:321`), а `has_more`,
`total` и пагинации в ответе нет — отличить «20 совпадений» от «200» вызывающий не
может.

---

## 4. Что уже можно рисовать

Всё перечисленное подтверждено кодом и не требует новых решений владельца.

1. **`Сегодня` — слой «Состояние» целиком.** `GET /api/v1/tenants/me/day/` отдаёт всё,
   что нужно утверждённой IA `Сейчас → Дальше → … → Мастера сегодня`: мастеров с их
   рабочими интервалами и перерывами, отсутствия, записи с локальным временем, услугой,
   длительностью, ценой, именем клиента, статусом и `version`, плюс закрытия салона и
   `summary.by_status`. Правило `current indicator = scheduled_start <= now <
   scheduled_end` считается на клиенте из `start_at`/`end_at` — новых полей не нужно.
2. **`Мастера сегодня`.** Мастера без записей возвращаются намеренно
   (`tenant_day_service.py:24-26`), так что «кто сегодня работает» и «у кого пусто»
   рисуется без доработок. Признак `schedule_source` (`weekly` / `exception`) уже есть
   — отклонение «сегодня по-особому» отличимо от обычного графика.
3. **Расписание на выбранную дату, режим `Все`.** `?date=YYYY-MM-DD` поддержан
   (`day_api.py:78-88`), данные по всем мастерам приходят одним ответом — единая
   хронология салона строится на клиенте.
4. **Режим конкретного мастера: рабочий день, записи, недоступность.** Всё в той же
   проекции. Не рисуется только слой «свободные интервалы» — см. раздел 5.
5. **Просмотр графика мастера и списка исключений.** GET-ручки недельного шаблона,
   исключений дат, недоступностей и закрытий салона существуют и скоуплены по тенанту.
6. **Постановка недоступности вместе с Conflict Guard — полностью.** Единственный
   сценарий, где authoritative impact preview, `impact_token`, отсутствие force-save,
   409 при устаревшем наборе и атомарное применение реализованы ровно так, как
   заморожено в DRF-1240. Можно рисовать без оговорок.
7. **Создание записи: клиент, услуга, мастер и подтверждение.** Поиск клиента, создание
   нового гостя «имя + телефон», обязательный `X-Idempotency-Key`, 409 «Это время
   занято» без force-create, authoritative результат — всё есть. Не рисуется только шаг
   выбора времени.
8. **Отмена записи.** Destructive confirmation, закрытый allowlist причин, доменная
   операция, authoritative readback.
9. **Перенос — в границах «другое время, тот же мастер».** Атомарная операция,
   `Было → Станет`, обязательный `expected_version`, 409 `STALE_VERSION`, исходная
   запись при конфликте не меняется.
10. **Завершение визита силами салона.** `SalonBookingCompleteView`
    (`tenants/appointments_api.py:624-739`) — блокировка, проверка версии, общая с
    мобильным путём доменная функция.
11. **Системные состояния Loading / Empty / Error / Conflict / Stale.** Формат ответов
    единый (`success_response` / `error_response`), коды ошибок стабильные
    (`SLOT_NOT_AVAILABLE`, `STALE_VERSION`, `IMPACT_CHANGED`, `HAS_ACTIVE_APPOINTMENTS`,
    `NOT_FOUND`, `IDEMPOTENCY_KEY_REQUIRED`) — под них рисовать можно.
12. **Правило «не показывать телефон клиента».** Выдержано на всей салонной
    поверхности: ни журнал дня, ни поиск клиента, ни impact preview телефона не
    отдают. Макет может опираться на это как на инвариант.

---

## 5. Что пока нельзя

| Что | Почему нельзя |
|---|---|
| **`Ayla · Изменения` — лента событий и счётчик «N новых»** | Нет read-API событий (P0-H, №9 матрицы), нет маркера «последний просмотр сотрудником» нигде в дереве, а атрибуция салонных действий сломана (P0-J). Рисовать ленту, которая будет утверждать «отменил клиент» вместо «отменили вы» — хуже, чем не рисовать |
| **`Требует внимания`** | Нет ни модели, ни поля, ни производного статуса. Единственный похожий кусок — таксономия в `appointments/records_status.py:77-99` — по собственному docstring (`:52-64`) в MVP не эмитится и является заглушкой |
| **Обращения клиентов («позвонил, но запись не создана»)** | Объекта не существует. Перебраны все модели репозитория, поиск по `обращен\|заявк\|лид` даёт ноль. Owner decision требование зафиксировал — реализации нет |
| **Цифровая передача смены** (ключевой сценарий Ayla по DRF-1249) | Складывается из двух предыдущих: сводка изменений за период + незакрытые ситуации. Ни того, ни другого |
| **Поиск времени для записи и `Найти время для записи`** | Салонной доступности нет ни одной ручки, многомастерного поиска нет вовсе (P0-K). Это блокирует шаг «Дата и время» в DRF-1238 и весь режим поиска в DRF-1237 |
| **Свободные интервалы в расписании мастера** | Read-путь отдаёт фиксированную сетку 30 минут, представления диапазона нет. «Свободный интервал — это диапазон доступности, а не готовый Appointment slot» (DRF-1237 freeze) сегодня backend'ом не выражается |
| **Перенос со сменой мастера** | P0-C |
| **Conflict Guard на «Не работаю», сокращении часов даты, изменении недельного дня и закрытии салона** | P0-A. Четыре из пяти операций, которые freeze требует накрыть, сегодня не накрыты |
| **Разрешение конфликта переносом затронутой записи** | P0-B — allowlist решений содержит только `cancel` |
| **Состояния «нет прав на действие при наличии прав на чтение»** | P0-E. Backend не различает. Рисовать экран, отличающий «смотрю» от «могу отменить», не под что |
| **Кабинет на карточке записи** | P0-I. Поля нет ни в модели, ни в проекции |
| **Маскированный телефон в разведении одноимённых клиентов** | P0-L. Backend телефон не отдаёт вообще, включая маскированный |
| **Любой салонный сценарий Ayla поверх бот-Bearer, кроме четырёх операций с записями и поиска клиента** | P0-D. Журнал дня, график, недоступность и закрытия боту недоступны в принципе |

---

## 6. Вопросы владельцу

Только те, где без решения работа встанет. Остальное — задачи, а не вопросы, и они
описаны в разделе 3.

### В-1. Каким способом Ayla вообще ходит в салонную поверхность?

Сегодня салон разрезан надвое (P0-D): записи пишутся бот-Bearer'ом, а день и график
читаются JWT администратора с `X-App-Type: pro`. Ayla попадает только в первую
половину. Развилки три, и они дают разную стоимость и разный контур безопасности:

* **A.** Вывести `/api/v1/tenants/me/day/` и весь график из-под `IsProApp` тем же
  приёмом, что DRF-1231 применил к `appointments/` и `customers/` — то есть добавить
  префиксы в `AppTypeMiddleware.EXCLUDED_PATH_PREFIXES` и перевести на
  `IsBotServiceWithVerifiedClient`. Дёшево, но расширяет поверхность, доступную по
  единому общему секрету.
* **B.** Оставить как есть и научить бота ходить JWT администратора. Меняет модель
  доверия бота, требует хранения пользовательских токенов.
* **C.** Отдельная read-поверхность для ассистента.

Выбор определяет, что именно рисуется в DRF-1249, и без него нельзя проектировать даже
уже готовый слой «Состояние». **Это первый вопрос, остальные вторичны.**

### В-2. Права в P0: «администратор может всё» или нужен настоящий ограниченный сотрудник?

Backend бинарен (P0-E): либо админ тенанта, либо ничего. `Role.STAFF` в модели есть,
но авторизацией не читается. Заморожённый UX (DRF-1239, DRF-1241) требует различать
право читать и право действовать.

Два варианта:

* **A.** Для Controlled Pilot принять «кто видит — тот и действует», и убрать из
  макетов permission-состояния. Ноль работы на backend, но заморозка расходится с уже
  утверждёнными комментариями DRF-1239/1241, и расхождение надо зафиксировать явно.
* **B.** Ввести реальный ограниченный сотрудник. Это новая машинерия — не «дописать
  роль», а завести понятие capability/scope, которого в кодовой базе нет вовсе.

### В-3. Перенос со сменой мастера — в P0 или нет?

Backend умеет только «другое время того же мастера» (P0-C), и это зафиксировано в коде
как осознанная граница Wave 1. Заморожённые формулировки DRF-1239 и DRF-1237 смену
исполнителя допускают.

Либо смена мастера уходит из P0 и формулировки DRF-1239/1237 правятся, либо в объём
входит «Replacement»-путь домена. Без решения нельзя рисовать flow переноса.

### В-4. Насколько широко Conflict Guard в P0?

Freeze DRF-1240 от 23.08 требует authoritative impact preview на пяти операциях,
реализована одна (P0-A). Полное покрытие — это impact preview и `resolutions` для
недельного шаблона, исключения даты и закрытия салона, то есть примерно троекратное
повторение самой дорогой части DRF-1062.

Либо P0 сужается до «Conflict Guard только на недоступности», и тогда остальные четыре
операции в UX должны быть либо запрещены, либо явно помечены как не защищённые, — либо
объём принимается целиком. Промежуточного безопасного варианта нет: сейчас эти четыре
операции проходят молча.

---

## Приложение А. Уточнения к замерам главного окна на 23.08

Проверено; расхождений по существу нет, но два места стоит поправить.

1. **«Адрес пуст у всех» — неточно.** Пуст у четырёх мастеров пилотного салона
   `formula-tela`. У всех пяти мастеров загруженных сегодня `mkt-*` арендаторов адрес
   заполнен (Пенза, конкретные улицы). Вывод про отсутствие города в Ayla при этом
   **верен**: в `tenants_tenant` колонок ровно шесть — `id, slug, name, is_active,
   created_at, updated_at`, поля города нет.
2. **Автозакрытие возвращает `None`, а не `{"ran": False}`** (`appointments/tasks.py:285`).
   На существо не влияет: возврат действительно молчаливый, строки в логе нет, а
   `BOOKING_AUTO_COMPLETE_ENABLED` в окружении `dev-web-1` отсутствует. DRF-1048
   подтверждён.
3. Остальные числа сошлись: 6 арендаторов, 9 мастеров (4 + 5), 8 записей (3
   `confirmed`, 5 `cancelled`, будущая одна — 24.08), 28 строк графика у пилота,
   `booking.completed` не существовало ни разу.

## Приложение Б. Что просится в задачи

Заводить не стал по запрету. Список для главного окна, по убыванию срочности:

1. Починить атрибуцию салонного актора — три вызова `envelope_actor_for` /
   `cancelled_by_for` вместо самодельных тернарников
   (`cancel_reschedule_service.py:251, 477, 492`). Дёшево, и без этого слой
   «Изменения» строить нельзя.
2. Выдать `formula-tela` администратора через `provision_salon_admin`; заодно
   поправить в команде `X-Tenant: {tenant.id}` на slug (`:132-136`).
3. Read-API операционных событий для тенанта под `/api/v1/tenants/me/` + колонка и
   индекс `tenant_id` на `OutboxEvent` (сейчас только внутри JSONB).
4. Маркер «последний просмотр изменений сотрудником» — новая модель, без неё нет
   «N новых».
5. События на изменения графика (четыре места записи).
6. Салонная ручка доступности + многомастерный поиск по услуге.
7. Conflict Guard на оставшиеся четыре операции сокращения доступности.
8. Модель обращения клиента и модель незакрытой ситуации.
9. Мелочь, но ломает диалог: `has_more`/`total` в ответе поиска клиента (P0-L).
